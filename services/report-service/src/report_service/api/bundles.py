"""POST/GET/DELETE/download для bulk relationship-report bundles (Phase 24.4).

Mirrors 24.3 ``api/relationship.py`` auth (X-User-Id header) и permission
gate (VIEWER+ через TreeMembership с trees.owner_user_id fallback).
24.3 sync API не модифицируется — эти ручки additive.

Endpoint paths:

* ``POST   /api/v1/trees/{tree_id}/report-bundles`` → 202 ``{job_id, total_count, queued_at}``
* ``GET    /api/v1/trees/{tree_id}/report-bundles/{job_id}`` → 200 status snapshot
* ``GET    /api/v1/trees/{tree_id}/report-bundles/{job_id}/download`` → 200 binary
* ``DELETE /api/v1/trees/{tree_id}/report-bundles/{job_id}`` → 204
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from shared_models import TreeRole, role_satisfies
from shared_models.orm import BundleStatus, ReportBundleJob, Tree, TreeMembership
from shared_models.storage import ObjectStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from report_service.bundles.data import (
    create_bundle_job,
    load_bundle_job,
    mark_cancelled,
)
from report_service.bundles.models import (
    BundleCreateRequest,
    BundleCreateResponse,
    BundleStatusSnapshot,
)
from report_service.bundles.storage import (
    content_type_for,
    get_bundle_storage,
)
from report_service.database import get_session
from report_service.queue import enqueue_bundle_job

router = APIRouter(prefix="/api/v1/trees", tags=["reports", "bundles"])

_LOG: Final = logging.getLogger(__name__)


def _parse_user_id_header(value: str | None) -> uuid.UUID:
    """X-User-Id → UUID. Mirrors 24.3 helper."""
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


async def _resolve_caller_role(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
) -> str | None:
    """Mirrors 24.3 ``api.relationship._resolve_caller_role``."""
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


async def _require_viewer(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
) -> None:
    """403→404 (mirror 11.0 leak-prevention) если caller не VIEWER+."""
    role = await _resolve_caller_role(session, user_id=user_id, tree_id=tree_id)
    if role is None or not role_satisfies(role, TreeRole.VIEWER):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {tree_id} not found or not accessible.",
        )


def _to_snapshot(job: ReportBundleJob) -> BundleStatusSnapshot:
    """ORM row → response DTO."""
    return BundleStatusSnapshot(
        job_id=job.id,
        tree_id=job.tree_id,
        status=job.status,  # Literal accepts string at runtime  # type: ignore[arg-type]
        output_format=job.output_format,
        total_count=job.total_count,
        completed_count=job.completed_count,
        failed_count=job.failed_count,
        error_summary=job.error_summary,  # list[dict] structurally compatible  # type: ignore[arg-type]
        storage_url=job.storage_url,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        ttl_expires_at=job.ttl_expires_at,
    )


def _serialize_pairs(body: BundleCreateRequest) -> list[dict[str, Any]]:
    """Pydantic → jsonb-friendly list. UUIDs → str, claim → enum value or None."""
    out: list[dict[str, Any]] = []
    for pair in body.relationship_pairs:
        out.append(
            {
                "person_a_id": str(pair.person_a_id),
                "person_b_id": str(pair.person_b_id),
                "claimed_relationship": (
                    pair.claimed_relationship.value
                    if pair.claimed_relationship is not None
                    else None
                ),
            }
        )
    return out


@router.post(
    "/{tree_id}/report-bundles",
    response_model=BundleCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a bulk relationship-report job",
)
async def create_bundle(
    tree_id: uuid.UUID,
    body: BundleCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> BundleCreateResponse:
    """Создать bundle job + поставить в arq-очередь.

    Worker (см. :mod:`report_service.bundles.runner`) подхватит job по
    ``generate_report_bundle_job(job_id)`` task'у. Caller получает 202
    + ``job_id`` для polling'а через ``GET .../{job_id}``.
    """
    user_id = _parse_user_id_header(x_user_id)
    await _require_viewer(session, user_id=user_id, tree_id=tree_id)

    pairs_payload = _serialize_pairs(body)
    job = await create_bundle_job(
        session,
        tree_id=tree_id,
        requested_by=user_id,
        relationship_pairs=pairs_payload,
        output_format=body.output_format,
        confidence_threshold=body.confidence_threshold,
    )
    await session.commit()

    try:
        await enqueue_bundle_job(job_id=str(job.id))
    except Exception:
        _LOG.exception(
            "bundle %s enqueued to DB but arq enqueue failed; "
            "ttl-cron will purge if worker doesn't pick it up",
            job.id,
        )

    return BundleCreateResponse(
        job_id=job.id,
        total_count=job.total_count,
        queued_at=job.created_at,
    )


@router.get(
    "/{tree_id}/report-bundles/{job_id}",
    response_model=BundleStatusSnapshot,
    summary="Bundle job status snapshot",
)
async def get_bundle_status(
    tree_id: uuid.UUID,
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> BundleStatusSnapshot:
    """Polling endpoint — caller дёргает каждые ~2s до status ∈ {completed, failed, cancelled}."""
    user_id = _parse_user_id_header(x_user_id)
    await _require_viewer(session, user_id=user_id, tree_id=tree_id)
    job = await load_bundle_job(session, job_id=job_id, tree_id=tree_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bundle {job_id} not found in tree {tree_id}.",
        )
    return _to_snapshot(job)


@router.get(
    "/{tree_id}/report-bundles/{job_id}/download",
    summary="Download the assembled bundle blob",
)
async def download_bundle(
    tree_id: uuid.UUID,
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_bundle_storage)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> StreamingResponse:
    """Stream blob (ZIP or PDF based on ``output_format``).

    Status codes per ADR-0078:
        * 200 — bundle ready, body — binary stream.
        * 404 — job not in this tree.
        * 409 — job not yet completed.
        * 410 — job completed but TTL passed (storage may already be purged).
    """
    user_id = _parse_user_id_header(x_user_id)
    await _require_viewer(session, user_id=user_id, tree_id=tree_id)
    job = await load_bundle_job(session, job_id=job_id, tree_id=tree_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bundle {job_id} not found in tree {tree_id}.",
        )
    if job.status != BundleStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bundle {job_id} status is {job.status!r}; not yet downloadable.",
        )
    now = dt.datetime.now(dt.UTC)
    if job.ttl_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Bundle {job_id} expired at {job.ttl_expires_at.isoformat()}.",
        )
    if not job.storage_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bundle {job_id} has no storage_url despite completed status.",
        )
    blob = await storage.get(job.storage_url)
    return StreamingResponse(
        iter([blob]),
        media_type=content_type_for(job.output_format),
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}.{_extension_for(job.output_format)}"',
        },
    )


@router.delete(
    "/{tree_id}/report-bundles/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a running bundle / cleanup completed",
)
async def delete_bundle(
    tree_id: uuid.UUID,
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_bundle_storage)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> Response:
    """Cancel running bundle / cleanup completed storage.

    Idempotent: повторный вызов на cancelled/already-purged → 204.
    """
    user_id = _parse_user_id_header(x_user_id)
    await _require_viewer(session, user_id=user_id, tree_id=tree_id)
    job = await load_bundle_job(session, job_id=job_id, tree_id=tree_id)
    if job is None:
        # Idempotent: 204 даже если row отсутствует.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if job.storage_url:
        try:
            await storage.delete(job.storage_url)
        except Exception:
            _LOG.warning("delete: storage.delete failed for %s", job.storage_url)

    if job.status in (BundleStatus.QUEUED.value, BundleStatus.RUNNING.value):
        await mark_cancelled(session, job_id=job_id)
        await session.commit()
    elif job.storage_url:
        # Completed job — cleanup storage_url но keep row (audit). Worker уже
        # завершён, просто стираем blob ссылку.
        from sqlalchemy import update  # noqa: PLC0415

        await session.execute(
            update(ReportBundleJob).where(ReportBundleJob.id == job_id).values(storage_url=None)
        )
        await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _extension_for(output_format: str) -> str:
    if output_format == "consolidated_pdf":
        return "pdf"
    return "zip"


__all__ = [
    "create_bundle",
    "delete_bundle",
    "download_bundle",
    "get_bundle_status",
    "router",
]
