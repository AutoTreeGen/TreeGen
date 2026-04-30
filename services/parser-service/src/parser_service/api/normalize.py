"""Phase 10.3 — AI normalization HTTP API (см. ADR-0060).

Endpoints:

* ``POST /places/normalize`` — нормализовать одну raw place-строку.
* ``POST /names/normalize`` — нормализовать одну raw person-name строку.

Обе ручки требуют Bearer JWT (ставится router-level в ``main.py``);
permission-gate тут не нужен — вход — личная raw-строка от user'а,
а не tree-scoped resource. Опциональные ``candidates`` приходят от
caller (UI / inference-pipeline) — caller сам отвечает за то, что
candidate-id'ы валидны в его контексте (мы их opaque-форвардим).

Cost-guards (зеркалит 10.2 ADR-0059):

* ``AI_LAYER_ENABLED=false`` → 503.
* Per-user-day rate limit + per-user-month tokens budget → 429.
* DRY_RUN env → mock-ответ без сетевых вызовов (для dev).
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis_asyncio
from ai_layer import (
    AILayerConfig,
    AILayerConfigError,
    AILayerDisabledError,
    BudgetExceededError,
    BudgetLimits,
    NameNormalizer,
    PlaceNormalizer,
)
from fastapi import APIRouter, Depends, HTTPException, status

from parser_service.auth import RequireUser
from parser_service.config import Settings, get_settings
from parser_service.schemas import (
    CandidateMatchResponse,
    NameNormalizationDetail,
    NameNormalizeRequest,
    NameNormalizeResponse,
    PlaceNormalizationDetail,
    PlaceNormalizeRequest,
    PlaceNormalizeResponse,
)
from parser_service.services.ai_normalization import (
    EmptyInputError,
    RawInputTooLargeError,
    build_name_normalizer,
    build_place_normalizer,
    run_name_normalization,
    run_place_normalization,
)

router = APIRouter()


# -----------------------------------------------------------------------------
# Dependencies — config / limits / clients. Mock-overridable в тестах через
# ``app.dependency_overrides[...]`` — никакого реального Anthropic в CI.
# -----------------------------------------------------------------------------


def get_ai_layer_config() -> AILayerConfig:
    """Свежий ``AILayerConfig`` из ENV — зеркалит ai_extraction.py."""
    return AILayerConfig.from_env()


def get_budget_limits(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BudgetLimits:
    """Перец-юзер budget-лимиты (одни и те же для extraction и normalization)."""
    return BudgetLimits(
        max_runs_per_day=settings.ai_max_runs_per_day,
        max_tokens_per_month=settings.ai_max_tokens_per_month,
    )


def get_place_normalizer(
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
) -> PlaceNormalizer:
    """Сборка place-normalizer'а; Voyage внутри лениво до первого call."""
    return build_place_normalizer(config)


def get_name_normalizer(
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
) -> NameNormalizer:
    return build_name_normalizer(config)


# Module-level фабрика — позволяет тестам подменять Redis-клиент через
# `_redis_client_factory = lambda: FakeRedis(...)`. Зеркалит pattern
# ``familysearch._make_redis_client`` (Phase 5.1).
_redis_client_factory: object = None


def _make_redis_client(settings: Settings) -> redis_asyncio.Redis:
    """Создать Redis-клиент для budget-counter'ов.

    Tests инжектят через ``_redis_client_factory``; production —
    ``redis.asyncio.Redis.from_url`` с ``decode_responses=True`` (нам
    нужен ``str`` для INCR / GET-ответов).
    """
    if _redis_client_factory is not None:
        client: redis_asyncio.Redis = _redis_client_factory()  # type: ignore[operator]
        return client
    return redis_asyncio.Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> redis_asyncio.Redis:
    """FastAPI dependency для Redis-клиента."""
    return _make_redis_client(settings)


# -----------------------------------------------------------------------------
# Exception → HTTP mapping.
# -----------------------------------------------------------------------------


def _raise_for_disabled(exc: AILayerDisabledError) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc),
    ) from exc


def _raise_for_config(exc: AILayerConfigError) -> None:
    # 503: misconfig — пользователь ничего не может сделать.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"AI layer is misconfigured: {exc}",
    ) from exc


def _raise_for_budget(exc: BudgetExceededError) -> None:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "message": str(exc),
            "limit_kind": exc.limit_kind,
            "limit_value": exc.limit_value,
            "current_value": exc.current_value,
        },
    ) from exc


def _raise_for_input(exc: EmptyInputError | RawInputTooLargeError) -> None:
    # 422: входная строка не подходит для нормализации.
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=str(exc),
    ) from exc


# -----------------------------------------------------------------------------
# Endpoints.
# -----------------------------------------------------------------------------


@router.post(
    "/places/normalize",
    response_model=PlaceNormalizeResponse,
    summary="Normalize one raw place string into a structured form (Phase 10.3).",
)
async def normalize_place(
    request: PlaceNormalizeRequest,
    user_id: RequireUser,
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
    limits: Annotated[BudgetLimits, Depends(get_budget_limits)],
    normalizer: Annotated[PlaceNormalizer, Depends(get_place_normalizer)],
    redis: Annotated[redis_asyncio.Redis, Depends(get_redis_client)],
) -> PlaceNormalizeResponse:
    try:
        result, report = await run_place_normalization(
            redis=redis,
            normalizer=normalizer,
            user_id=user_id,
            raw=request.raw,
            locale_hint=request.locale_hint,
            context=request.context,
            candidates=request.candidates,
            top_k=request.top_k,
            config=config,
            limits=limits,
        )
    except AILayerDisabledError as exc:
        _raise_for_disabled(exc)
    except AILayerConfigError as exc:
        _raise_for_config(exc)
    except BudgetExceededError as exc:
        _raise_for_budget(exc)
    except (EmptyInputError, RawInputTooLargeError) as exc:
        _raise_for_input(exc)

    place = result.place
    if place is None:
        # Сюда не попадаем (PlaceNormalizer всегда заполняет .place); guard
        # для type-checker'а.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Place normalization returned empty result",
        )

    return PlaceNormalizeResponse(
        place=PlaceNormalizationDetail(**place.model_dump()),
        candidates=[CandidateMatchResponse(**c.model_dump()) for c in result.candidates],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        model=result.model,
        dry_run=result.dry_run,
        budget_remaining_runs=report.remaining_runs,
    )


@router.post(
    "/names/normalize",
    response_model=NameNormalizeResponse,
    summary="Normalize one raw person-name string into a structured form (Phase 10.3).",
)
async def normalize_name(
    request: NameNormalizeRequest,
    user_id: RequireUser,
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
    limits: Annotated[BudgetLimits, Depends(get_budget_limits)],
    normalizer: Annotated[NameNormalizer, Depends(get_name_normalizer)],
    redis: Annotated[redis_asyncio.Redis, Depends(get_redis_client)],
) -> NameNormalizeResponse:
    try:
        result, report = await run_name_normalization(
            redis=redis,
            normalizer=normalizer,
            user_id=user_id,
            raw=request.raw,
            script_hint=request.script_hint,
            locale_hint=request.locale_hint,
            context=request.context,
            candidates=request.candidates,
            top_k=request.top_k,
            config=config,
            limits=limits,
        )
    except AILayerDisabledError as exc:
        _raise_for_disabled(exc)
    except AILayerConfigError as exc:
        _raise_for_config(exc)
    except BudgetExceededError as exc:
        _raise_for_budget(exc)
    except (EmptyInputError, RawInputTooLargeError) as exc:
        _raise_for_input(exc)

    name = result.name
    if name is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Name normalization returned empty result",
        )

    return NameNormalizeResponse(
        name=NameNormalizationDetail(**name.model_dump()),
        candidates=[CandidateMatchResponse(**c.model_dump()) for c in result.candidates],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        model=result.model,
        dry_run=result.dry_run,
        budget_remaining_runs=report.remaining_runs,
    )
