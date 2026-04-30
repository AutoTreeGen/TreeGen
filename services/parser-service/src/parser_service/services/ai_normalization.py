"""Phase 10.3 — AI normalization service helpers (см. ADR-0060).

Связывает три слоя:

1. ``ai_layer`` use cases — :class:`PlaceNormalizer` / :class:`NameNormalizer`
   + clients + budget evaluation. Без зависимости на ORM.
2. **Redis** — counter-based per-user budget (мы НЕ заводим ORM-таблицу
   для normalize-runs: каждый вызов независим и idempotent, persist'ить
   нечего; cost-tracking хватает Redis-counter'ов с TTL). Это ADR-0060
   §«Storage cost telemetry — Redis vs ORM table».
3. parser-service API endpoint'ы — приходят из ``api/normalize.py`` и
   вызывают функции этого модуля.

Контракт budget-учёта совместим с Phase 10.2 (ADR-0059) на уровне
семантики (``BudgetLimits`` / ``BudgetReport`` / ``evaluate_budget``);
единственное отличие — implementation бэкенд (Redis вместо
``source_extractions`` SQL-aggregation). Phase 10.5 (биллинг)
унифицирует когда понадобится cross-use-case бюджет.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any, Protocol

from ai_layer import (
    AILayerConfig,
    AnthropicClient,
    BudgetLimits,
    BudgetReport,
    CandidateRecord,
    NameNormalizer,
    NormalizationResult,
    PlaceNormalizer,
    VoyageEmbeddingClient,
    ensure_ai_layer_enabled,
    evaluate_budget,
    log_ai_usage,
)
from ai_layer.use_cases.normalize import (
    EmptyInputError,
    NormalizationError,
    RawInputTooLargeError,
)

_logger = logging.getLogger(__name__)

#: Формат day-bucket ключей Redis. Calendar-day buckets (UTC) — упрощают
#: «скользящее окно 24h» до «текущий день»; для лимита 10/day разница
#: незначительна, а реализация сильно проще ZSET-based.
_DAY_FORMAT = "%Y%m%d"
#: TTL day-bucket runs-ключа: 24h + buffer на clock skew между serv'ами.
_RUNS_TTL_SECONDS = 24 * 60 * 60 + 600
#: TTL day-bucket tokens-ключа: 30d + buffer.
_TOKENS_TTL_SECONDS = 30 * 24 * 60 * 60 + 600
#: Сколько day-keys читать для «месячного» token budget'а.
_MONTH_WINDOW_DAYS = 30


class _RedisLike(Protocol):
    """Минимальный async-Redis интерфейс, который нам нужен.

    Совместим с ``redis.asyncio.Redis`` и ``fakeredis.aioredis.FakeRedis``.
    Включает ``lpush`` чтобы тот же клиент можно было прокинуть в
    ``ai_layer.log_ai_usage`` (он LPUSH'ит в общий audit-LIST).
    """

    async def incr(self, name: str) -> int: ...
    async def incrby(self, name: str, amount: int) -> int: ...
    async def expire(self, name: str, time: int) -> Any: ...
    async def get(self, name: str) -> Any: ...
    async def mget(self, keys: list[str]) -> list[Any]: ...
    async def lpush(self, name: str, *values: str) -> Any: ...


def _runs_key(user_id: uuid.UUID, day: str) -> str:
    return f"ai_norm:runs:{user_id}:{day}"


def _tokens_key(user_id: uuid.UUID, day: str) -> str:
    return f"ai_norm:tokens:{user_id}:{day}"


def _coerce_int(value: Any) -> int:
    """Distill Redis-ответ в int (bytes / str / int / None all handled)."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, bytes):
        return int(value.decode() or 0)
    if isinstance(value, str):
        return int(value or 0) if value.strip() else 0
    return int(value)


# -----------------------------------------------------------------------------
# Budget tracking — Redis counter + sliding 30-day window summed across days.
# -----------------------------------------------------------------------------


async def record_normalize_usage(
    redis: _RedisLike,
    *,
    user_id: uuid.UUID,
    tokens_used: int,
    now: dt.datetime | None = None,
) -> None:
    """Увеличить per-user-day counter'ы для runs / tokens.

    Telemetry-режим: failure не валит use-case (пишем warning и
    продолжаем). Зеркалит контракт ``log_ai_usage`` из Phase 10.1.
    """
    moment = now or dt.datetime.now(dt.UTC)
    day = moment.strftime(_DAY_FORMAT)
    runs_key = _runs_key(user_id, day)
    try:
        await redis.incr(runs_key)
        await redis.expire(runs_key, _RUNS_TTL_SECONDS)
        if tokens_used > 0:
            tokens_key = _tokens_key(user_id, day)
            await redis.incrby(tokens_key, tokens_used)
            await redis.expire(tokens_key, _TOKENS_TTL_SECONDS)
    except Exception:
        _logger.warning(
            "ai-normalization usage record failed",
            extra={"user_id": str(user_id), "tokens_used": tokens_used},
            exc_info=True,
        )


async def compute_normalize_budget_report(
    redis: _RedisLike,
    *,
    user_id: uuid.UUID,
    limits: BudgetLimits,
    now: dt.datetime | None = None,
) -> BudgetReport:
    """Собрать ``BudgetReport`` из day-bucket Redis counters.

    Failure-mode: если Redis недоступен, возвращаем zero-usage отчёт
    (gate effectively-open). Это сознательное решение —
    `feedback_no_admin_merge.md`-аналог: «отказ Redis'а не должен
    блокировать продакшн endpoint». Альтернатива (fail-closed: 503)
    могла бы остановить сервис целиком из-за telemetry-проблемы.

    Phase 10.5 (биллинг с реальными деньгами) поменяет на fail-closed
    + alert.
    """
    moment = now or dt.datetime.now(dt.UTC)
    today_key = _runs_key(user_id, moment.strftime(_DAY_FORMAT))
    try:
        today_runs = _coerce_int(await redis.get(today_key))
        token_keys = [
            _tokens_key(user_id, (moment - dt.timedelta(days=i)).strftime(_DAY_FORMAT))
            for i in range(_MONTH_WINDOW_DAYS)
        ]
        token_values = await redis.mget(token_keys)
        tokens_30d = sum(_coerce_int(v) for v in token_values)
    except Exception:
        _logger.warning(
            "ai-normalization budget read failed; allowing the call (fail-open)",
            extra={"user_id": str(user_id)},
            exc_info=True,
        )
        return BudgetReport(runs_in_last_24h=0, tokens_in_last_30d=0, limits=limits)

    return BudgetReport(
        runs_in_last_24h=today_runs,
        tokens_in_last_30d=tokens_30d,
        limits=limits,
    )


# -----------------------------------------------------------------------------
# Construction helpers — keep api/normalize.py thin.
# -----------------------------------------------------------------------------


def build_place_normalizer(config: AILayerConfig) -> PlaceNormalizer:
    """Собрать ``PlaceNormalizer`` из ``AILayerConfig``.

    Voyage инстанциируется с тем же config'ом; если ``VOYAGE_API_KEY``
    пустой и caller передаст candidates — Voyage-вызов поднимет
    ``AILayerConfigError``, который use-case логирует и возвращает
    пустой ``candidates``-список. См. ``PlaceNormalizer.normalize``.
    """
    return PlaceNormalizer(
        AnthropicClient(config),
        voyage=VoyageEmbeddingClient(config),
    )


def build_name_normalizer(config: AILayerConfig) -> NameNormalizer:
    """Собрать ``NameNormalizer``."""
    return NameNormalizer(
        AnthropicClient(config),
        voyage=VoyageEmbeddingClient(config),
    )


def _to_candidate_records(
    items: list[Any],
) -> list[CandidateRecord]:
    """Перегнать API-schema candidate'ов в ai-layer-domain объекты."""
    return [CandidateRecord(id=item.id, text=item.text) for item in items]


# -----------------------------------------------------------------------------
# Orchestrator — gates → budget → call → telemetry.
# -----------------------------------------------------------------------------


async def run_place_normalization(
    *,
    redis: _RedisLike,
    normalizer: PlaceNormalizer,
    user_id: uuid.UUID,
    raw: str,
    locale_hint: str | None,
    context: str | None,
    candidates: list[Any],
    top_k: int,
    config: AILayerConfig,
    limits: BudgetLimits,
) -> tuple[NormalizationResult, BudgetReport]:
    """Полный flow для place-normalize endpoint'а.

    Контракт зеркалит ``run_source_extraction`` из 10.2:

    1. ``ensure_ai_layer_enabled(config)`` — kill-switch.
    2. ``compute_normalize_budget_report`` + ``evaluate_budget`` — rate
       limit + tokens budget.
    3. ``normalizer.normalize(...)`` — Anthropic + опциональный Voyage.
    4. ``record_normalize_usage`` — счётчики Redis.
    5. ``log_ai_usage`` — телеметрия для cross-use-case аналитики.

    Возвращает ``(result, budget_report)`` — отчёт нужен endpoint'у,
    чтобы вернуть ``budget_remaining_runs`` без второго round-trip'а
    в Redis.
    """
    ensure_ai_layer_enabled(config)

    report = await compute_normalize_budget_report(
        redis,
        user_id=user_id,
        limits=limits,
    )
    evaluate_budget(report)

    result = await normalizer.normalize(
        raw,
        locale_hint=locale_hint,  # type: ignore[arg-type]
        context=context,
        candidates=_to_candidate_records(candidates),
        top_k=top_k,
    )

    if not result.dry_run and result.tokens_used > 0:
        await record_normalize_usage(
            redis,
            user_id=user_id,
            tokens_used=result.tokens_used,
        )
        await log_ai_usage(
            redis=redis,
            use_case="normalize_place",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            user_id=user_id,
        )
        # Re-read для свежего remaining_runs в endpoint-ответе.
        report = await compute_normalize_budget_report(
            redis,
            user_id=user_id,
            limits=limits,
        )

    return result, report


async def run_name_normalization(
    *,
    redis: _RedisLike,
    normalizer: NameNormalizer,
    user_id: uuid.UUID,
    raw: str,
    script_hint: str | None,
    locale_hint: str | None,
    context: str | None,
    candidates: list[Any],
    top_k: int,
    config: AILayerConfig,
    limits: BudgetLimits,
) -> tuple[NormalizationResult, BudgetReport]:
    """Полный flow для name-normalize endpoint'а (mirror of place)."""
    ensure_ai_layer_enabled(config)

    report = await compute_normalize_budget_report(
        redis,
        user_id=user_id,
        limits=limits,
    )
    evaluate_budget(report)

    result = await normalizer.normalize(
        raw,
        script_hint=script_hint,  # type: ignore[arg-type]
        locale_hint=locale_hint,  # type: ignore[arg-type]
        context=context,
        candidates=_to_candidate_records(candidates),
        top_k=top_k,
    )

    if not result.dry_run and result.tokens_used > 0:
        await record_normalize_usage(
            redis,
            user_id=user_id,
            tokens_used=result.tokens_used,
        )
        await log_ai_usage(
            redis=redis,
            use_case="normalize_name",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            user_id=user_id,
        )
        report = await compute_normalize_budget_report(
            redis,
            user_id=user_id,
            limits=limits,
        )

    return result, report


__all__ = [
    "EmptyInputError",
    "NormalizationError",
    "RawInputTooLargeError",
    "build_name_normalizer",
    "build_place_normalizer",
    "compute_normalize_budget_report",
    "record_normalize_usage",
    "run_name_normalization",
    "run_place_normalization",
]
