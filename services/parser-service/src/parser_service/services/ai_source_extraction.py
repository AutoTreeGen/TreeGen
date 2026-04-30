"""Phase 10.2 — AI source-extraction service helpers (см. ADR-0059).

Этот модуль связывает три слоя:

1. ``ai_layer`` — generic LLM use-case (``SourceExtractor``), kill-switch,
   budget. Без зависимости на ORM.
2. ``shared_models.orm`` — таблицы ``source_extractions`` и
   ``extracted_facts``.
3. parser-service API endpoints — приходят из ``api/ai_extraction.py``
   и вызывают функции этого модуля.

Что здесь делается:

* ``compute_user_budget_report`` — собирает текущее usage'е user'а из
  ``source_extractions`` за окно [now-24h, now-30d) и возвращает
  :class:`ai_layer.BudgetReport`. На Phase 10.1 (hypothesis runner)
  эта функция дополнится сложением ``hypothesis_runs`` — отдельная
  generic-функция в ``ai_layer.budget`` тогда станет осмысленной;
  пока simple-impl здесь.
* ``extract_text_from_pdf`` — обёртка над pypdf с quality-threshold;
  возвращает текст или ``None`` если quality плохая.
* ``run_source_extraction`` — orchestrator: проверяет gates + budget,
  создаёт ``SourceExtraction(PENDING)``, вызывает ``SourceExtractor``,
  персистит ``ExtractedFact`` rows, обновляет статус.
* ``accept_extracted_fact`` / ``reject_extracted_fact`` — review actions.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from ai_layer import (
    AILayerConfig,
    AIRunStatus,
    AnthropicClient,
    BudgetExceededError,
    BudgetLimits,
    BudgetReport,
    ImageInput,
    SourceExtractor,
    SourceMetadata,
    build_raw_response,
    ensure_ai_layer_enabled,
    estimate_extraction_cost_usd,
    estimate_input_tokens_from_image,
    estimate_input_tokens_from_text,
    evaluate_budget,
)
from ai_layer.use_cases.source_extraction import (
    DocumentTooLargeError,
    EmptyDocumentError,
    FabricatedQuoteError,
    SourceExtractionError,
)
from pydantic import ValidationError as PydanticValidationError
from shared_models.enums import SourceType
from shared_models.orm import ExtractedFact, Source, SourceExtraction
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)

# pypdf-quality threshold: PDF, давший < 50 печатных символов на всю
# книгу, скорее всего отсканирован как изображение без OCR — text path
# бесполезен, нужен vision-fallback. ADR-0059 §«Vision + PDF strategy».
_PDF_TEXT_QUALITY_MIN_CHARS = 50


class AISourceExtractionError(RuntimeError):
    """Базовый класс ошибок этого слоя — для catch-all в API handler'ах."""


class DnaSourceForbiddenError(AISourceExtractionError):
    """Попытка извлечь факты из DNA-source. Нарушение ADR-0043 §Privacy."""


class SourceNotFoundError(AISourceExtractionError):
    """Source row не существует / soft-deleted."""


class PoorPdfQualityError(AISourceExtractionError):
    """pypdf вернул < ``_PDF_TEXT_QUALITY_MIN_CHARS`` чистого текста."""


@dataclass(frozen=True)
class ExtractionRunResult:
    """Возвращаемое значение ``run_source_extraction``.

    Attributes:
        extraction: Сохранённая ``SourceExtraction`` row (status=COMPLETED
            на happy path; FAILED при exception'е до commit'а).
        fact_count: Сколько ``ExtractedFact`` rows было создано.
    """

    extraction: SourceExtraction
    fact_count: int


# -----------------------------------------------------------------------------
# Budget computation — простая реализация для Phase 10.2a (только source
# extraction). Phase 10.1+ хук: добавим sum по hypothesis_runs.
# -----------------------------------------------------------------------------


async def compute_user_budget_report(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limits: BudgetLimits,
    now: dt.datetime | None = None,
) -> BudgetReport:
    """Собрать текущее usage user'а за rolling 24h/30d окна.

    Считает только ``source_extractions``. Phase 10.1 будет добавлять
    hypothesis_runs вторым SUM'ом, и тогда вынесем в ``ai_layer.budget``
    как Protocol-обобщение. Преждевременная generalization сейчас не
    оправдана.

    ``COMPLETED`` и ``FAILED`` status обе считаются (FAILED тоже
    тратит токены, если LLM ответил, но Pydantic упал на validation).
    ``PENDING`` исключаются — это inflight'ы, ещё не зафиксированный
    cost.
    """
    moment = now or dt.datetime.now(dt.UTC)
    last_24h = moment - dt.timedelta(hours=24)
    last_30d = moment - dt.timedelta(days=30)

    runs_24h = await session.scalar(
        select(func.count(SourceExtraction.id)).where(
            SourceExtraction.requested_by_user_id == user_id,
            SourceExtraction.status.in_([AIRunStatus.COMPLETED.value, AIRunStatus.FAILED.value]),
            SourceExtraction.created_at >= last_24h,
        )
    )
    tokens_30d = await session.scalar(
        select(
            func.coalesce(
                func.sum(SourceExtraction.input_tokens + SourceExtraction.output_tokens),
                0,
            )
        ).where(
            SourceExtraction.requested_by_user_id == user_id,
            SourceExtraction.status == AIRunStatus.COMPLETED.value,
            SourceExtraction.created_at >= last_30d,
        )
    )
    return BudgetReport(
        runs_in_last_24h=int(runs_24h or 0),
        tokens_in_last_30d=int(tokens_30d or 0),
        limits=limits,
    )


# -----------------------------------------------------------------------------
# PDF text extraction.
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PdfFallbackImage:
    """Phase 10.2b — image, извлечённый из scanned PDF для vision-fallback'а.

    Attributes:
        image_bytes: Raw bytes изображения (caller передаёт в
            ``image_preprocessing.preprocess_image``).
        media_type: MIME type — ``image/jpeg`` или ``image/png``;
            определяется pypdf'ом по embedded-format'у.
        page_index: Номер страницы (0-based), на которой нашёлся image.
            Caller использует для UI «vision-fallback применён к стр. N».
    """

    image_bytes: bytes
    media_type: str
    page_index: int


def extract_first_image_from_scanned_pdf(pdf_bytes: bytes) -> PdfFallbackImage:
    """Phase 10.2b — извлечь первое embedded image из scanned PDF.

    Сценарий: пользователь загрузил PDF, который на самом деле — серия
    отсканированных страниц (один image-блок на страницу). pypdf-text
    extraction вернул < ``_PDF_TEXT_QUALITY_MIN_CHARS`` (см.
    :class:`PoorPdfQualityError`); вместо того чтобы фейлить, тянем raw
    image из первой страницы и отправляем в Claude vision через image
    preprocessing.

    Покрывает 90% scanned-PDF сценариев (один большой JPEG/PNG на
    страницу). Не покрывает: vector-PDF без embedded images
    (теоретически невозможно, потому что для них pypdf-text работает),
    multi-image страницы (берём первый — для метрик/писем достаточно;
    сложные случаи в Phase 10.4 через PyMuPDF rendering).

    Raises:
        AISourceExtractionError: pypdf не открыл PDF, или ни одной
            страницы с embedded image не нашлось.
    """
    try:
        from pypdf import PdfReader  # noqa: PLC0415
        from pypdf.errors import PdfReadError  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        msg = "pypdf is not installed; cannot extract images from PDF"
        raise AISourceExtractionError(msg) from exc

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except (PdfReadError, ValueError, OSError) as exc:
        msg = f"PDF parse failed: {exc}"
        raise AISourceExtractionError(msg) from exc

    for page_index, page in enumerate(reader.pages):
        try:
            images = list(page.images)
        except Exception as exc:
            # pypdf API на странных PDF'ах кидает разные исключения —
            # broad-catch чтобы один битый page не валил весь fallback.
            _logger.warning("pypdf page.images failed on page %d: %s", page_index, exc)
            continue
        if not images:
            continue
        first = images[0]
        image_data = getattr(first, "data", None)
        if not image_data:
            continue
        # pypdf возвращает name типа "/Image1.jpg"; парсим extension для media_type.
        name = getattr(first, "name", "") or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        media_type = (
            "image/jpeg"
            if ext in {"jpg", "jpeg"}
            else "image/png"
            if ext == "png"
            else "image/jpeg"  # fallback — JPEG как default scanned-PDF format
        )
        return PdfFallbackImage(
            image_bytes=image_data,
            media_type=media_type,
            page_index=page_index,
        )

    msg = (
        "PDF text extraction yielded too little text, and no embedded "
        "images found in the first pages — vision fallback impossible. "
        "Consider re-uploading individual page images via /ai-extract-vision."
    )
    raise AISourceExtractionError(msg)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Извлечь plain text из PDF-байтов через pypdf.

    Returns:
        Строка с конкатенированным текстом всех страниц. Пустая строка,
        если pypdf не нашёл текст (image-only scan).

    Raises:
        PoorPdfQualityError: text < ``_PDF_TEXT_QUALITY_MIN_CHARS``.
            Caller может ловить и попробовать vision-fallback.
        AISourceExtractionError: pypdf не смог открыть PDF (битый файл).
    """
    try:
        # Лениво импортируем pypdf: он добавлен в parser-service deps,
        # но не нужен в каждом импорте (PLC0415-исключение в pyproject).
        from pypdf import PdfReader  # noqa: PLC0415
        from pypdf.errors import PdfReadError  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dep missing in CI
        msg = "pypdf is not installed; cannot extract PDF text"
        raise AISourceExtractionError(msg) from exc

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except (PdfReadError, ValueError, OSError) as exc:
        msg = f"PDF parse failed: {exc}"
        raise AISourceExtractionError(msg) from exc

    pages_text: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            # pypdf бросает разнотипные исключения на испорченных
            # страницах (ValueError / KeyError / AttributeError /
            # PdfReadError на уровне страницы). Ловим broad чтобы
            # одна плохая страница не валила весь документ.
            _logger.warning("pypdf failed on a page: %s", exc)
            continue
        pages_text.append(text)

    full_text = "\n\n".join(pages_text).strip()
    if len(full_text) < _PDF_TEXT_QUALITY_MIN_CHARS:
        msg = (
            f"PDF text too short ({len(full_text)} chars) — likely an "
            "image-only scan. Vision fallback recommended."
        )
        raise PoorPdfQualityError(msg)
    return full_text


# -----------------------------------------------------------------------------
# Privacy + budget gates.
# -----------------------------------------------------------------------------


async def fetch_source_or_404(session: AsyncSession, source_id: uuid.UUID) -> Source:
    """Получить ``Source`` row с soft-delete-фильтром.

    Raises:
        SourceNotFoundError: row не существует или deleted_at != NULL.
    """
    src = (
        await session.execute(
            select(Source).where(
                Source.id == source_id,
                Source.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if src is None:
        msg = f"Source {source_id} not found"
        raise SourceNotFoundError(msg)
    return src


def assert_source_not_dna(source: Source) -> None:
    """ADR-0043 §Privacy: запрет AI-extraction на DNA-source'ах.

    Raises:
        DnaSourceForbiddenError: ``source.source_type == 'dna_test'``.
    """
    if source.source_type == SourceType.DNA_TEST.value:
        msg = (
            f"Source {source.id} is DNA-marked (source_type='dna_test'); "
            "AI extraction is forbidden by ADR-0043 §Privacy."
        )
        raise DnaSourceForbiddenError(msg)


# -----------------------------------------------------------------------------
# Main orchestrator.
# -----------------------------------------------------------------------------


def _enforce_per_source_cost_cap(
    *,
    estimated_input_tokens: int,
    max_output_tokens: int,
    model: str,
    cap_usd: float,
) -> None:
    """Phase 10.2b — pre-flight per-source $-cap.

    ``cap_usd <= 0`` отключает гейт (env override / dev-mode). Иначе:
    оцениваем worst-case стоимость через ``estimate_extraction_cost_usd``
    и поднимаем :class:`BudgetExceededError` если оценка > cap'а.

    Параллельный per-user 24h/30d guard (``evaluate_budget``) ловит
    cumulative abuse; этот предотвращает один разорительный документ
    (например, 200k-symbol PDF, который в одиночку съест 10× обычного
    бюджета).
    """
    if cap_usd <= 0:
        return
    estimated = estimate_extraction_cost_usd(
        model=model,
        estimated_input_tokens=estimated_input_tokens,
        max_output_tokens=max_output_tokens,
    )
    if estimated > cap_usd:
        # Используем существующий BudgetExceededError для того же UX
        # 429-ответа: caller ловит один тип, разные limit_kind'ы.
        # Multiplied 1e4 на int — мы конвертируем в «cents × 100», чтобы
        # удовлетворить int-типу полей (limit_kind фиксирован, остальные
        # int). Caller рендерит обратно в $ для UI.
        raise BudgetExceededError(
            limit_kind="cost_per_source_usd_x10000",
            limit_value=round(cap_usd * 10_000),
            current_value=round(estimated * 10_000),
        )


async def run_source_extraction(
    session: AsyncSession,
    *,
    source: Source,
    document_text: str,
    user_id: uuid.UUID,
    config: AILayerConfig,
    limits: BudgetLimits,
    extractor: SourceExtractor,
    cost_cap_usd: float = 0.0,
    image: ImageInput | None = None,
) -> ExtractionRunResult:
    """Запустить full extraction flow: gates → call → persist.

    Контракт:

    1. ``ensure_ai_layer_enabled(config)`` — kill-switch.
    2. ``assert_source_not_dna(source)`` — privacy.
    3. ``evaluate_budget(report)`` — rate limit + token budget per-user.
    4. Phase 10.2b: ``_enforce_per_source_cost_cap`` — отдельный per-source
       $-cap до отправки в Claude.
    5. Создание ``SourceExtraction(status=PENDING)`` (commit'нем после
       завершения, чтобы не оставлять висящих PENDING при rollback).
    6. ``extractor.extract_from_text(...)`` или ``extract_from_image(...)``
       (если ``image is not None``) — Claude.
    7. Парсинг ответа в ``ExtractedFact`` rows.
    8. Status → COMPLETED, токены, raw_response. Commit.

    На любом exception'е status выставляется FAILED и row commit'ится
    (cost-tracking всё равно: input-tokens были потрачены, даже если
    output невалиден). Caller получает exception обратно для UI.

    Args:
        cost_cap_usd: Per-source $ cap. ``0.0`` отключает гейт.
        image: Опциональный image-input для vision-режима. Если задан,
            ``document_text`` интерпретируется как ``ocr_text_hint`` для
            quote-validation (см. ``SourceExtractor.extract_from_image``).
    """
    ensure_ai_layer_enabled(config)
    assert_source_not_dna(source)

    report = await compute_user_budget_report(session, user_id=user_id, limits=limits)
    evaluate_budget(report)

    # Phase 10.2b: per-source pre-flight cost cap.
    if image is None:
        estimated_in = estimate_input_tokens_from_text(len(document_text))
    else:
        estimated_in = estimate_input_tokens_from_image(
            ocr_text_hint_length_chars=len(document_text) if document_text else 0,
        )
    _enforce_per_source_cost_cap(
        estimated_input_tokens=estimated_in,
        max_output_tokens=extractor.max_tokens,
        model=config.anthropic_model,
        cap_usd=cost_cap_usd,
    )

    extraction = SourceExtraction(
        source_id=source.id,
        tree_id=source.tree_id,
        requested_by_user_id=user_id,
        model_version=config.anthropic_model,
        prompt_version="source_extractor_v1",
        status=AIRunStatus.PENDING.value,
        input_tokens=0,
        output_tokens=0,
        raw_response={},
    )
    session.add(extraction)
    await session.flush()

    metadata = SourceMetadata(
        title=source.title,
        author=source.author,
        source_type=source.source_type,
        date=str(source.publication_date) if source.publication_date else None,
        place=source.repository,
    )

    try:
        if image is None:
            completion = await extractor.extract_from_text(document_text, metadata)
        else:
            # Vision-mode: document_text используется как ocr_text_hint для
            # quote-validation. Если caller не предоставил OCR-hint
            # (vision-only), text будет sentinel "[image-only document...]"
            # из use-case'а — quote-validation пропустится автоматически.
            completion = await extractor.extract_from_image(
                image,
                metadata,
                ocr_text_hint=document_text if document_text else None,
            )
    except (
        EmptyDocumentError,
        DocumentTooLargeError,
        FabricatedQuoteError,
        SourceExtractionError,
        PydanticValidationError,
    ) as exc:
        extraction.status = AIRunStatus.FAILED.value
        extraction.error = _truncate(str(exc), 2000)
        extraction.completed_at = dt.datetime.now(dt.UTC)
        await session.commit()
        raise

    extraction.input_tokens = completion.input_tokens
    extraction.output_tokens = completion.output_tokens
    extraction.raw_response = build_raw_response(
        completion=completion,
        prompt_version="source_extractor_v1",
    )
    extraction.status = AIRunStatus.COMPLETED.value
    extraction.completed_at = dt.datetime.now(dt.UTC)

    fact_count = 0
    for person in completion.parsed.persons:
        session.add(
            ExtractedFact(
                extraction_id=extraction.id,
                fact_index=fact_count,
                fact_kind="person",
                data=person.model_dump(mode="json"),
                confidence=person.confidence,
                status="pending",
            )
        )
        fact_count += 1
    for event in completion.parsed.events:
        session.add(
            ExtractedFact(
                extraction_id=extraction.id,
                fact_index=fact_count,
                fact_kind="event",
                data=event.model_dump(mode="json"),
                confidence=event.confidence,
                status="pending",
            )
        )
        fact_count += 1
    for relationship in completion.parsed.relationships:
        session.add(
            ExtractedFact(
                extraction_id=extraction.id,
                fact_index=fact_count,
                fact_kind="relationship",
                data=relationship.model_dump(mode="json"),
                confidence=relationship.confidence,
                status="pending",
            )
        )
        fact_count += 1

    await session.commit()
    return ExtractionRunResult(extraction=extraction, fact_count=fact_count)


# -----------------------------------------------------------------------------
# Review actions.
# -----------------------------------------------------------------------------


async def accept_extracted_fact(
    session: AsyncSession,
    *,
    fact: ExtractedFact,
    reviewed_by_user_id: uuid.UUID,
    note: str | None = None,
) -> ExtractedFact:
    """Mark fact as accepted. Запись доменной сущности — caller'у.

    Phase 10.2a не материализует ``Person``/``Event``/``Citation`` rows
    автоматически — это решение review-UI: edit-before-accept часто
    меняет имя/дату до сохранения. После accept'а здесь, frontend
    делает отдельные POST /persons / POST /events с этой fact-row как
    ``provenance.ai_extraction_id``. См. ADR-0059 §«Persistence shape».
    """
    fact.status = "accepted"
    fact.reviewed_at = dt.datetime.now(dt.UTC)
    fact.reviewed_by_user_id = reviewed_by_user_id
    fact.review_note = _truncate(note, 1024) if note else None
    await session.commit()
    return fact


async def reject_extracted_fact(
    session: AsyncSession,
    *,
    fact: ExtractedFact,
    reviewed_by_user_id: uuid.UUID,
    note: str | None = None,
) -> ExtractedFact:
    """Mark fact as rejected. Не удаляет row — audit-trail сохраняется."""
    fact.status = "rejected"
    fact.reviewed_at = dt.datetime.now(dt.UTC)
    fact.reviewed_by_user_id = reviewed_by_user_id
    fact.review_note = _truncate(note, 1024) if note else None
    await session.commit()
    return fact


# -----------------------------------------------------------------------------
# Construction helpers — keep main.py / api/ai_extraction.py thin.
# -----------------------------------------------------------------------------


def build_extractor(config: AILayerConfig) -> SourceExtractor:
    """Собрать ``SourceExtractor`` из ``AILayerConfig``.

    Caller'ам не нужно знать про ``AnthropicClient`` инстанциирование —
    только про config + extractor. Тесты подменяют через FastAPI
    ``dependency_overrides``.
    """
    return SourceExtractor(AnthropicClient(config))


def _truncate(value: str | None, max_len: int) -> str | None:
    """Урезать строку до ``max_len`` символов с многоточием."""
    if value is None:
        return None
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


__all__ = [
    "AISourceExtractionError",
    "BudgetExceededError",
    "DnaSourceForbiddenError",
    "ExtractionRunResult",
    "PdfFallbackImage",
    "PoorPdfQualityError",
    "SourceNotFoundError",
    "accept_extracted_fact",
    "assert_source_not_dna",
    "build_extractor",
    "compute_user_budget_report",
    "extract_first_image_from_scanned_pdf",
    "extract_text_from_pdf",
    "fetch_source_or_404",
    "reject_extracted_fact",
    "run_source_extraction",
]


# Re-export нужных Any-бэкендов для удобства caller'ов внутри
# parser-service. (Внешний код всё равно импортирует напрямую.)
_ = Any
