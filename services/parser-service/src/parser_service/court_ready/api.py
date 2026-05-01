"""POST /api/v1/reports/court-ready endpoint.

Phase 15.6 — defensible PDF report. Sync (no arq job): typical отчёт
(один person, ≤50 events, ≤10 relationships) рендерится за <1s. Если
объём вырастет, перевести на arq job симметрично 4.11a/b GDPR exporter
(``services.user_export_runner``).

Permission: requires VIEWER+ роль на tree персоны через
:func:`require_person_tree_role`.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from shared_models import TreeRole
from shared_models.orm import User
from shared_models.storage import (
    InMemoryStorage,
    ObjectStorage,
    SignedUrl,
    build_storage_from_env,
)
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import get_current_user
from parser_service.court_ready.data import build_report_context
from parser_service.court_ready.models import (
    CourtReadyReportRequest,
    CourtReadyReportResponse,
)
from parser_service.court_ready.render import (
    PdfRenderError,
    render_html,
    render_pdf,
)
from parser_service.database import get_session
from parser_service.services.permissions import check_tree_permission

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)

# Signed URL TTL: 24h по умолчанию — достаточно чтобы пользователь успел
# скачать в браузере и переслать клиенту. Если нужно длиннее — вынести
# в settings.
_DEFAULT_URL_TTL_SECONDS: Final[int] = 24 * 3600

_STORAGE_KEY_PREFIX: Final[str] = "court-ready-reports"


# Storage dep — overridable из тестов через ``app.dependency_overrides``.
def get_report_storage() -> ObjectStorage:
    """Construct storage backend by env. См. shared_models.storage.

    Симметрично ``parser_service.api.users.get_export_storage`` — отдельная
    функция чтобы тесты могли подменить independently от GDPR-export'а.
    """
    return build_storage_from_env()


def _storage_key(report_id: uuid.UUID, person_id: uuid.UUID) -> str:
    """Каноническая раскладка key'а: ``court-ready-reports/{person}/{report_id}.pdf``.

    Person-id префиксом — даёт object-lifecycle / GDPR-erasure-by-prefix
    делать selective cleanup на удаление person'а. Симметрично GDPR-export
    раскладке (``gdpr-exports/{user_id}/{request_id}.zip``).
    """
    return f"{_STORAGE_KEY_PREFIX}/{person_id}/{report_id}.pdf"


@router.post(
    "/api/v1/reports/court-ready",
    response_model=CourtReadyReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a Court-Ready PDF report for a person/family/ancestry line",
)
async def generate_court_ready_report(
    body: CourtReadyReportRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[ObjectStorage, Depends(get_report_storage)],
) -> CourtReadyReportResponse:
    """Возвращает signed-URL на сгенерированный PDF.

    Алгоритм:

    1. Проверить, что caller имеет VIEWER+ на tree person'а (404 если person
       не существует, 403 если нет роли).
    2. Собрать ``ReportContext`` через ``build_report_context``.
    3. Рендер HTML → PDF через WeasyPrint. Если native libs отсутствуют —
       503 (UI должен отобразить «PDF недоступен на этой инсталляции»).
    4. Положить blob в ObjectStorage, вернуть signed download URL.

    Body:
        ``person_id`` — UUID персоны.
        ``scope`` ∈ {person, family, ancestry_to_gen}. Default ``person``.
        ``target_gen`` — int 1..12, обязателен только для scope=ancestry_to_gen.
        ``locale`` ∈ {en, ru}. Default ``en``.
    """
    # Permission gate. Резолвим Person.tree_id через первый шаг
    # build_report_context — но он сам raise'нет KeyError. Вынесем в
    # отдельный preflight чтобы 404 vs 403 был корректным.
    from shared_models.orm import Person  # noqa: PLC0415  — local to this gate
    from sqlalchemy import select  # noqa: PLC0415

    tree_id = await session.scalar(
        select(Person.tree_id).where(Person.id == body.person_id, Person.deleted_at.is_(None))
    )
    if tree_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person {body.person_id} not found",
        )
    if not await check_tree_permission(
        session,
        user_id=user.id,
        tree_id=tree_id,
        required=TreeRole.VIEWER,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User {user.id} does not have viewer access on tree {tree_id}",
        )

    if body.scope == "ancestry_to_gen" and body.target_gen is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_gen is required when scope='ancestry_to_gen'",
        )

    # Researcher = display name пользователя; fallback на email.
    researcher = (user.display_name or user.email or "").strip() or None

    context = await build_report_context(
        session,
        person_id=body.person_id,
        scope=body.scope,
        target_gen=body.target_gen,
        locale=body.locale,
        researcher_name=researcher,
    )

    html = render_html(context)
    try:
        pdf_bytes = render_pdf(html)
    except PdfRenderError as exc:
        _LOG.warning("PDF render failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF rendering is not available in this environment.",
        ) from exc

    key = _storage_key(context.report_id, body.person_id)
    await storage.put(key, pdf_bytes, content_type="application/pdf")
    signed: SignedUrl = await storage.signed_download_url(
        key, expires_in_seconds=_DEFAULT_URL_TTL_SECONDS
    )

    # Phase 15.6: in-memory storage для тестов даёт ``memory://...`` URL;
    # production gives presigned-S3 / GCS — оба валидны. UI просто
    # выставляет href и ничего не парсит.
    _LOG.info(
        "court_ready report generated: person=%s scope=%s tree=%s report=%s pdf_bytes=%d",
        body.person_id,
        body.scope,
        tree_id,
        context.report_id,
        len(pdf_bytes),
    )

    # NB: ``InMemoryStorage`` — fake URL, expires_at от self-signed; всё ок
    # для tests + dev. Прод-storage честно возвращает signed URL.
    _ = isinstance(storage, InMemoryStorage)  # explicit no-op для линтера

    return CourtReadyReportResponse(
        report_id=context.report_id,
        pdf_url=signed.url,
        expires_at=_ensure_aware(signed.expires_at),
    )


def _ensure_aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


__all__ = ["get_report_storage", "router"]
