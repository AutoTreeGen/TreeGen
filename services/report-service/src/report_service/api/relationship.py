"""POST /api/v1/reports/relationship + GET /api/v1/reports/{report_id}.

Phase 24.3 — sync relationship-report generation. Type. отчёт (один pair,
≤ 50 evidence pieces) рендерится за < 1s; если объём вырастет, перевести
на arq job (зеркало 4.11a/b GDPR exporter и court-ready 15.6 если он
переедет на async).

Auth — `X-User-Id` header в стиле billing-service до Phase 4.10. Upstream
API gateway / Clerk JWT-сидекар обязан валидировать токен и проставлять
header. Permission gate: VIEWER+ роль на дереве через TreeMembership.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, status
from shared_models import TreeRole, role_satisfies
from shared_models.orm import Tree, TreeMembership
from shared_models.storage import (
    ObjectStorage,
    SignedUrl,
    build_storage_from_env,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from report_service.config import get_settings
from report_service.database import get_session
from report_service.relationship.data import build_report_context
from report_service.relationship.models import (
    RelationshipReportRequest,
    RelationshipReportResponse,
)
from report_service.relationship.render import (
    PdfRenderError,
    render_html,
    render_pdf,
)

router = APIRouter(prefix="/api/v1/reports", tags=["reports", "relationship"])

_LOG: Final = logging.getLogger(__name__)

_STORAGE_KEY_PREFIX: Final[str] = "relationship-reports"


def get_report_storage() -> ObjectStorage:
    """Construct storage backend by env. См. shared_models.storage."""
    return build_storage_from_env()


def _storage_key(report_id: uuid.UUID, tree_id: uuid.UUID) -> str:
    """Layout: ``relationship-reports/{tree_id}/{report_id}.pdf``.

    Tree-prefix даёт object-lifecycle / GDPR-erasure-by-prefix чистить
    отчёты при удалении дерева. Симметрично 15.6 (``court-ready-reports/{person_id}/...``).
    """
    return f"{_STORAGE_KEY_PREFIX}/{tree_id}/{report_id}.pdf"


async def _resolve_caller_role(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
) -> str | None:
    """Активная роль user'а в tree. None — нет membership и нет owner-fallback'а."""
    role = await session.scalar(
        select(TreeMembership.role).where(
            TreeMembership.tree_id == tree_id,
            TreeMembership.user_id == user_id,
            TreeMembership.revoked_at.is_(None),
        )
    )
    if role is not None:
        return role
    owner_id = await session.scalar(select(Tree.owner_user_id).where(Tree.id == tree_id))
    if owner_id is not None and owner_id == user_id:
        return TreeRole.OWNER.value
    return None


def _ensure_aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _parse_user_id_header(value: str | None) -> uuid.UUID:
    if not value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required.",
        )
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header must be a UUID.",
        ) from exc


@router.post(
    "/relationship",
    response_model=RelationshipReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a per-relationship research-report PDF",
)
async def generate_relationship_report(
    body: RelationshipReportRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_report_storage)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> RelationshipReportResponse:
    """Сгенерировать relationship report — sync PDF.

    Алгоритм:

    1. Резолвим X-User-Id → проверяем VIEWER+ роль на ``body.tree_id``.
    2. Собираем ``RelationshipReportContext`` через
       :func:`build_report_context` (KeyError → 404 если tree/person отсутствуют).
    3. Рендерим HTML → PDF через WeasyPrint. Если native libs нет — 503.
    4. Сохраняем blob в ObjectStorage; возвращаем signed download URL.

    Body validation:
        ``person_a_id != person_b_id`` — иначе 400.
    """
    user_id = _parse_user_id_header(x_user_id)

    if body.person_a_id == body.person_b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="person_a_id and person_b_id must differ.",
        )

    role = await _resolve_caller_role(session, user_id=user_id, tree_id=body.tree_id)
    if role is None or not role_satisfies(role, TreeRole.VIEWER):
        # 404 vs 403: если caller вообще не видит tree, говорим 404 чтобы
        # не утекать информацию о существовании дерева. Mirrors 11.0 behavior.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {body.tree_id} not found or not accessible.",
        )

    try:
        context = await build_report_context(
            session,
            tree_id=body.tree_id,
            person_a_id=body.person_a_id,
            person_b_id=body.person_b_id,
            claim=body.claimed_relationship,
            locale=body.options.locale,
            title_style=body.options.title_style,
            include_dna_evidence=body.options.include_dna_evidence,
            include_archive_evidence=body.options.include_archive_evidence,
            include_hypothesis_flags=body.options.include_hypothesis_flags,
            researcher_name=None,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    html = render_html(context)
    try:
        pdf_bytes = render_pdf(html)
    except PdfRenderError as exc:
        _LOG.warning("PDF render failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF rendering is not available in this environment.",
        ) from exc

    key = _storage_key(context.report_id, body.tree_id)
    await storage.put(key, pdf_bytes, content_type="application/pdf")
    settings = get_settings()
    signed: SignedUrl = await storage.signed_download_url(
        key, expires_in_seconds=settings.pdf_url_ttl_seconds
    )

    _LOG.info(
        "relationship report generated: tree=%s pair=(%s, %s) claim=%s "
        "report=%s pdf_bytes=%d evidence=%d counter=%d confidence=%.2f",
        body.tree_id,
        body.person_a_id,
        body.person_b_id,
        body.claimed_relationship.value,
        context.report_id,
        len(pdf_bytes),
        len(context.evidence),
        len(context.counter_evidence),
        context.confidence,
    )

    return RelationshipReportResponse(
        report_id=context.report_id,
        pdf_url=signed.url,
        expires_at=_ensure_aware(signed.expires_at),
        confidence=context.confidence,
        evidence_count=len(context.evidence),
        counter_evidence_count=len(context.counter_evidence),
    )


__all__ = ["generate_relationship_report", "get_report_storage", "router"]
