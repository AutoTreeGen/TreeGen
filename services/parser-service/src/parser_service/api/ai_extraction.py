"""Phase 10.2 — AI source-extraction HTTP API (см. ADR-0059).

Эндпоинты (все scoped к одному ``Source``):

* ``POST /sources/{id}/ai-extract`` — trigger extraction.
* ``GET /sources/{id}/extracted-facts`` — list runs + facts.
* ``POST /sources/{id}/extracted-facts/{fact_id}/accept`` — review.
* ``POST /sources/{id}/extracted-facts/{fact_id}/reject`` — review.

Auth — router-level Bearer JWT (см. ``main.py``); permission gate
делегирован сюда (resolve source → tree, then ``check_tree_permission``).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from ai_layer import (
    AILayerConfig,
    AILayerDisabledError,
    BudgetExceededError,
    BudgetLimits,
    SourceExtractor,
    estimate_cost_usd,
    estimate_extraction_cost_usd,
    estimate_input_tokens_from_image,
)
from ai_layer.use_cases.source_extraction import SourceExtractionError
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from shared_models import TreeRole
from shared_models.orm import ExtractedFact, Source, SourceExtraction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import RequireUser
from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.schemas import (
    AIExtractedFactDetail,
    AIExtractionDetail,
    AIExtractionFactsResponse,
    AIExtractStatusResponse,
    AIExtractTriggerRequest,
    AIExtractTriggerResponse,
    AIExtractVisionResponse,
    AIFactReviewRequest,
)
from parser_service.services.ai_source_extraction import (
    DnaSourceForbiddenError,
    SourceNotFoundError,
    accept_extracted_fact,
    build_extractor,
    compute_user_budget_report,
    fetch_source_or_404,
    reject_extracted_fact,
    run_source_extraction,
)
from parser_service.services.image_preprocessing import (
    SUPPORTED_MEDIA_TYPES,
    CorruptImageError,
    UnsupportedImageFormatError,
    normalize_media_type,
    preprocess_image,
)
from parser_service.services.permissions import check_tree_permission

# Soft cap для одного uploaded image: 25 МБ — после preprocess'а почти
# гарантированно ≤ 1 МБ, до preprocess'а 25 МБ дают комфортный запас
# для исходных фотографий с современных смартфонов (40-50 MP RAW).
_MAX_IMAGE_UPLOAD_BYTES: int = 25 * 1024 * 1024

router = APIRouter()


# -----------------------------------------------------------------------------
# Dependencies — resolve config + extractor lazily.
# -----------------------------------------------------------------------------


def get_ai_layer_config() -> AILayerConfig:
    """Собрать ``AILayerConfig`` из ENV.

    Каждый вызов читает свежий env — это позволяет тестам ставить
    ``AI_LAYER_ENABLED=true`` через fixture'ы и видеть эффект без
    cache-инвалидации.
    """
    return AILayerConfig.from_env()


def get_budget_limits(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BudgetLimits:
    """Собрать ``BudgetLimits`` из parser-service settings."""
    return BudgetLimits(
        max_runs_per_day=settings.ai_max_runs_per_day,
        max_tokens_per_month=settings.ai_max_tokens_per_month,
    )


def get_extract_budget_usd(
    settings: Annotated[Settings, Depends(get_settings)],
) -> float:
    """Phase 10.2b: per-source $-cap из settings."""
    return settings.extract_budget_usd


def get_source_extractor(
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
) -> SourceExtractor:
    """Собрать ``SourceExtractor``.

    Тесты подменяют через ``app.dependency_overrides[get_source_extractor]``
    — никакого реального Anthropic API в CI.
    """
    return build_extractor(config)


# -----------------------------------------------------------------------------
# Permission helpers.
# -----------------------------------------------------------------------------


async def _require_editor_on_source(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Source:
    """Resolve source → проверить EDITOR-роль user'а в tree.

    AI extraction — write-action (создаёт ``SourceExtraction`` row в
    дереве). Минимальная роль — EDITOR. ADR-0036 §«Roles».

    404 если source не найден (privacy: не различаем «нет source'а»
    и «нет доступа к tree»).
    """
    try:
        source = await fetch_source_or_404(session, source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    has_role = await check_tree_permission(
        session,
        user_id=user_id,
        tree_id=source.tree_id,
        required=TreeRole.EDITOR,
    )
    if not has_role:
        # 404 vs 403: для cross-tree user'а возвращаем 404 чтобы не
        # утечь существование source'а. Для in-tree user'а с
        # недостаточной ролью — было бы 403, но distinguish'ить сложно
        # без дополнительного round-trip'а; consistent 404 — fail-closed.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )
    return source


# -----------------------------------------------------------------------------
# Endpoints.
# -----------------------------------------------------------------------------


@router.post(
    "/sources/{source_id}/ai-extract",
    response_model=AIExtractTriggerResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sources", "ai"],
    summary="Trigger AI extraction для одного Source.",
    description=(
        "Запускает Claude-extraction на тексте источника. Sync-mode на "
        "10.2a (typical 1–10 сек). 503 если AI_LAYER_ENABLED=false; "
        "422 если source DNA-marked; 429 если budget exceeded. См. "
        "ADR-0059."
    ),
)
async def trigger_extract(
    source_id: uuid.UUID,
    body: AIExtractTriggerRequest,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
    limits: Annotated[BudgetLimits, Depends(get_budget_limits)],
    extractor: Annotated[SourceExtractor, Depends(get_source_extractor)],
    cost_cap_usd: Annotated[float, Depends(get_extract_budget_usd)],
) -> AIExtractTriggerResponse:
    source = await _require_editor_on_source(session, source_id=source_id, user_id=user_id)

    document_text = body.document_text or source.text_excerpt
    if not document_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Source has no text content; pass `document_text` in the "
                "request body (e.g. extracted via pypdf for PDF uploads)."
            ),
        )

    try:
        result = await run_source_extraction(
            session,
            source=source,
            document_text=document_text,
            user_id=user_id,
            config=config,
            limits=limits,
            extractor=extractor,
            cost_cap_usd=cost_cap_usd,
        )
    except AILayerDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DnaSourceForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": str(exc),
                "limit_kind": exc.limit_kind,
                "limit_value": exc.limit_value,
                "current_value": exc.current_value,
            },
        ) from exc
    except SourceExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Re-compute budget report после run'а, чтобы UI показал свежее
    # remaining без отдельного round-trip'а.
    report = await compute_user_budget_report(session, user_id=user_id, limits=limits)

    return AIExtractTriggerResponse(
        extraction=AIExtractionDetail.model_validate(result.extraction),
        fact_count=result.fact_count,
        budget_remaining_runs=report.remaining_runs,
        budget_remaining_tokens=report.remaining_tokens,
    )


# -----------------------------------------------------------------------------
# Phase 10.2b — vision endpoint + status endpoint.
# -----------------------------------------------------------------------------


@router.post(
    "/sources/{source_id}/ai-extract-vision",
    response_model=AIExtractVisionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sources", "ai", "vision"],
    summary="Phase 10.2b — vision extraction для image-источников.",
    description=(
        "Multipart upload изображения (jpeg / png / gif / webp). Сервис "
        "auto-rotate'ит EXIF, ресайзит >2048px и вызывает Claude vision. "
        "Тот же набор гейтов что и `/ai-extract`: kill-switch, DNA-source "
        "filter, per-user 24h/30d budget, per-source $-cap. См. ADR-0059 "
        "§Phase 10.2b deltas."
    ),
)
async def trigger_extract_vision(
    source_id: uuid.UUID,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    config: Annotated[AILayerConfig, Depends(get_ai_layer_config)],
    limits: Annotated[BudgetLimits, Depends(get_budget_limits)],
    extractor: Annotated[SourceExtractor, Depends(get_source_extractor)],
    cost_cap_usd: Annotated[float, Depends(get_extract_budget_usd)],
    image: Annotated[UploadFile, File(description="Image file (≤25 MB).")],
    ocr_text_hint: Annotated[
        str | None,
        Form(
            description=(
                "Optional OCR-результат как text-hint для quote validation. "
                "None → vision-only режим, quote-validation пропускается."
            ),
        ),
    ] = None,
) -> AIExtractVisionResponse:
    source = await _require_editor_on_source(session, source_id=source_id, user_id=user_id)

    # Размер: streaming-чтение upload'а с лимитом — не доверяем
    # ``image.size`` (может быть None или фейковым на multipart-уровне).
    raw_bytes = await image.read()
    if len(raw_bytes) > _MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Image upload exceeds limit: {len(raw_bytes)} bytes > "
                f"{_MAX_IMAGE_UPLOAD_BYTES}. Resize before upload."
            ),
        )
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Empty image upload.",
        )

    raw_media_type = image.content_type or "image/jpeg"
    normalized_type = normalize_media_type(raw_media_type)
    if normalized_type not in SUPPORTED_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported image media_type {raw_media_type!r}; "
                f"Anthropic vision accepts {sorted(SUPPORTED_MEDIA_TYPES)}."
            ),
        )

    try:
        prepared = preprocess_image(raw_bytes, normalized_type)
    except UnsupportedImageFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except CorruptImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    estimated_cost = estimate_extraction_cost_usd(
        model=config.anthropic_model,
        estimated_input_tokens=estimate_input_tokens_from_image(
            ocr_text_hint_length_chars=len(ocr_text_hint) if ocr_text_hint else 0,
        ),
        max_output_tokens=4096,
    )

    try:
        result = await run_source_extraction(
            session,
            source=source,
            document_text=ocr_text_hint or "",
            user_id=user_id,
            config=config,
            limits=limits,
            extractor=extractor,
            cost_cap_usd=cost_cap_usd,
            image=prepared.image_input,
        )
    except AILayerDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DnaSourceForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": str(exc),
                "limit_kind": exc.limit_kind,
                "limit_value": exc.limit_value,
                "current_value": exc.current_value,
            },
        ) from exc
    except SourceExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    report = await compute_user_budget_report(session, user_id=user_id, limits=limits)

    return AIExtractVisionResponse(
        extraction=AIExtractionDetail.model_validate(result.extraction),
        fact_count=result.fact_count,
        budget_remaining_runs=report.remaining_runs,
        budget_remaining_tokens=report.remaining_tokens,
        estimated_cost_usd=estimated_cost,
        image_was_resized=prepared.was_resized,
        image_was_rotated=prepared.was_rotated,
        image_original_bytes=prepared.original_size_bytes,
        image_processed_bytes=prepared.processed_size_bytes,
    )


@router.get(
    "/sources/{source_id}/ai-extract-status",
    response_model=AIExtractStatusResponse,
    tags=["sources", "ai"],
    summary="Phase 10.2b — последний extraction-run + текущие budget'ы.",
    description=(
        "Возвращает наиболее свежий ``SourceExtraction`` для source'а "
        "(status / tokens / cost) плюс remaining-budget user'а. На "
        "10.2b sync-mode значение почти сразу будет COMPLETED/FAILED; "
        "10.2c добавит async-arq и PENDING станет реальным переходным "
        "состоянием. Frontend опрашивает этот endpoint для progress UI."
    ),
)
async def get_extract_status(
    source_id: uuid.UUID,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    limits: Annotated[BudgetLimits, Depends(get_budget_limits)],
    cost_cap_usd: Annotated[float, Depends(get_extract_budget_usd)],
) -> AIExtractStatusResponse:
    try:
        source = await fetch_source_or_404(session, source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    has_role = await check_tree_permission(
        session,
        user_id=user_id,
        tree_id=source.tree_id,
        required=TreeRole.VIEWER,
    )
    if not has_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )

    last_run_res = await session.execute(
        select(SourceExtraction)
        .where(SourceExtraction.source_id == source_id)
        .order_by(SourceExtraction.created_at.desc())
        .limit(1)
    )
    last_run = last_run_res.scalar_one_or_none()

    fact_count = 0
    cost_usd = 0.0
    extraction_detail: AIExtractionDetail | None = None
    if last_run is not None:
        fact_res = await session.execute(
            select(ExtractedFact).where(ExtractedFact.extraction_id == last_run.id)
        )
        fact_count = len(list(fact_res.scalars().all()))
        cost_usd = estimate_cost_usd(
            model=last_run.model_version,
            input_tokens=last_run.input_tokens,
            output_tokens=last_run.output_tokens,
        )
        extraction_detail = AIExtractionDetail.model_validate(last_run)

    report = await compute_user_budget_report(session, user_id=user_id, limits=limits)

    return AIExtractStatusResponse(
        source_id=source_id,
        has_extraction=last_run is not None,
        extraction=extraction_detail,
        fact_count=fact_count,
        cost_usd=cost_usd,
        budget_remaining_runs=report.remaining_runs,
        budget_remaining_tokens=report.remaining_tokens,
        extract_budget_usd=cost_cap_usd,
    )


@router.get(
    "/sources/{source_id}/extracted-facts",
    response_model=AIExtractionFactsResponse,
    tags=["sources", "ai"],
    summary="Перечислить все extraction-runs и их facts для одного Source.",
)
async def list_extracted_facts(
    source_id: uuid.UUID,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AIExtractionFactsResponse:
    # VIEWER достаточно: только чтение.
    try:
        source = await fetch_source_or_404(session, source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    has_role = await check_tree_permission(
        session,
        user_id=user_id,
        tree_id=source.tree_id,
        required=TreeRole.VIEWER,
    )
    if not has_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )

    runs_res = await session.execute(
        select(SourceExtraction)
        .where(SourceExtraction.source_id == source_id)
        .order_by(SourceExtraction.created_at.desc())
    )
    runs = list(runs_res.scalars().all())
    run_ids = [r.id for r in runs]

    facts: list[ExtractedFact] = []
    if run_ids:
        facts_res = await session.execute(
            select(ExtractedFact)
            .where(ExtractedFact.extraction_id.in_(run_ids))
            .order_by(ExtractedFact.extraction_id, ExtractedFact.fact_index)
        )
        facts = list(facts_res.scalars().all())

    return AIExtractionFactsResponse(
        source_id=source_id,
        extractions=[AIExtractionDetail.model_validate(r) for r in runs],
        facts=[AIExtractedFactDetail.model_validate(f) for f in facts],
    )


@router.post(
    "/sources/{source_id}/extracted-facts/{fact_id}/accept",
    response_model=AIExtractedFactDetail,
    tags=["sources", "ai"],
    summary="Принять extracted fact (status → accepted).",
    description=(
        "Помечает fact как accepted с reviewer'ом и timestamp'ом. "
        "Не материализует доменные сущности — это решение review-UI; "
        "frontend делает отдельный POST /persons / /events с "
        "``provenance.ai_extraction_id`` = run-id (10.2b)."
    ),
)
async def accept_fact(
    source_id: uuid.UUID,
    fact_id: uuid.UUID,
    body: AIFactReviewRequest,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AIExtractedFactDetail:
    fact = await _fetch_fact_with_perms(
        session,
        source_id=source_id,
        fact_id=fact_id,
        user_id=user_id,
    )
    if body.data is not None:
        fact.data = body.data
    updated = await accept_extracted_fact(
        session,
        fact=fact,
        reviewed_by_user_id=user_id,
        note=body.note,
    )
    return AIExtractedFactDetail.model_validate(updated)


@router.post(
    "/sources/{source_id}/extracted-facts/{fact_id}/reject",
    response_model=AIExtractedFactDetail,
    tags=["sources", "ai"],
    summary="Отклонить extracted fact (status → rejected).",
)
async def reject_fact(
    source_id: uuid.UUID,
    fact_id: uuid.UUID,
    body: AIFactReviewRequest,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AIExtractedFactDetail:
    fact = await _fetch_fact_with_perms(
        session,
        source_id=source_id,
        fact_id=fact_id,
        user_id=user_id,
    )
    updated = await reject_extracted_fact(
        session,
        fact=fact,
        reviewed_by_user_id=user_id,
        note=body.note,
    )
    return AIExtractedFactDetail.model_validate(updated)


# -----------------------------------------------------------------------------
# Helpers (private).
# -----------------------------------------------------------------------------


async def _fetch_fact_with_perms(
    session: AsyncSession,
    *,
    source_id: uuid.UUID,
    fact_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ExtractedFact:
    """Resolve fact + extraction + source + проверить EDITOR-role.

    Один JOIN: fact → extraction → source → tree. 404 на любом
    отсутствующем уровне (privacy-cautious: не различаем «нет fact'а»
    от «не твоё дерево»).
    """
    # Privacy-fail-closed: source — фактический owner permission'а.
    # Проверяем, что fact.extraction.source_id == path source_id.
    res = await session.execute(
        select(ExtractedFact, SourceExtraction, Source)
        .join(
            SourceExtraction,
            SourceExtraction.id == ExtractedFact.extraction_id,
        )
        .join(Source, Source.id == SourceExtraction.source_id)
        .where(
            ExtractedFact.id == fact_id,
            Source.id == source_id,
            Source.deleted_at.is_(None),
        )
    )
    row = res.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extracted fact {fact_id} not found on source {source_id}",
        )
    # SA 2.0 переименовало Row.tuple() → Row._tuple(); leading-underscore
    # тут — публичный API SQLAlchemy, не приватный member.
    fact, _extraction, source = row._tuple()
    has_role = await check_tree_permission(
        session,
        user_id=user_id,
        tree_id=source.tree_id,
        required=TreeRole.EDITOR,
    )
    if not has_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extracted fact {fact_id} not found on source {source_id}",
        )
    if fact.status != "pending":
        # Идемпотент: повторный accept на already-accepted fact'е —
        # 409 (clarity > silent no-op). UI должен прятать accept/reject
        # кнопки если status != pending.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Fact already reviewed (status={fact.status!r}); cannot transition again."),
        )

    # Suppress unused-var warning for _extraction (мы не достаём из неё ничего,
    # join нужен только для resolution chain).
    _ = _extraction
    return fact


__all__ = ["router"]
