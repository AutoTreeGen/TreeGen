"""FS-flagged dedup-attempts review API (Phase 5.2.1).

Эндпоинты:

* ``GET  /trees/{tree_id}/dedup-attempts?status=pending&limit=50`` —
  список attempt'ов; фильтр по virtual ``status`` (производное от пары
  ``rejected_at`` / ``merged_at``).
* ``POST /dedup-attempts/{id}/reject`` — пометить attempt отказанным
  (выставляет ``rejected_at = now()``); body опциональный
  ``{reason?: str}``.
* ``POST /dedup-attempts/{id}/merge`` — пометить attempt'у ``merged_at``
  и вернуть URL Phase 4.6 merge-flow'а; body обязателен
  ``{confirm: true}``.

Никакого автомата merge — сам merge выполняется на Phase 4.6
``POST /persons/{id}/merge`` после явного выбора survivor'а user'ом.
Этот endpoint только проставляет timestamp на attempt-row и отдаёт
relative-URL обработчика. См. CLAUDE.md §5 + ADR-0022.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import FsDedupAttempt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.schemas import (
    FsDedupAttemptListResponse,
    FsDedupAttemptMergeRequest,
    FsDedupAttemptMergeResponse,
    FsDedupAttemptOut,
    FsDedupAttemptRejectRequest,
    FsDedupAttemptStatus,
)

router = APIRouter()

_STATUS_VALUES = frozenset(get_args(FsDedupAttemptStatus))


def _to_out(row: FsDedupAttempt) -> FsDedupAttemptOut:
    """Сконструировать ``FsDedupAttemptOut`` с производным ``status``."""
    derived: Literal["pending", "rejected", "merged"]
    if row.merged_at is not None:
        derived = "merged"
    elif row.rejected_at is not None:
        derived = "rejected"
    else:
        derived = "pending"
    return FsDedupAttemptOut(
        id=row.id,
        tree_id=row.tree_id,
        fs_person_id=row.fs_person_id,
        candidate_person_id=row.candidate_person_id,
        score=row.score,
        reason=row.reason,
        fs_pid=row.fs_pid,
        rejected_at=row.rejected_at,
        merged_at=row.merged_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        provenance=row.provenance or {},
        status=derived,
    )


@router.get(
    "/trees/{tree_id}/dedup-attempts",
    response_model=FsDedupAttemptListResponse,
    tags=["dedup-attempts"],
    summary="List FS-flagged dedup attempts for a tree.",
)
async def list_fs_dedup_attempts(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    status_q: FsDedupAttemptStatus = Query(
        default="pending",
        alias="status",
        description=(
            "Виртуальный статус: ``pending`` (default) — оба timestamp'а NULL; "
            "``rejected`` — выставлен ``rejected_at``; ``merged`` — выставлен "
            "``merged_at``; ``all`` — без фильтра."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> FsDedupAttemptListResponse:
    """Список attempt'ов в дереве с пагинацией и фильтром по статусу."""
    if status_q not in _STATUS_VALUES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown status: {status_q}",
        )

    base = select(FsDedupAttempt).where(FsDedupAttempt.tree_id == tree_id)
    if status_q == "pending":
        base = base.where(
            FsDedupAttempt.rejected_at.is_(None),
            FsDedupAttempt.merged_at.is_(None),
        )
    elif status_q == "rejected":
        base = base.where(FsDedupAttempt.rejected_at.isnot(None))
    elif status_q == "merged":
        base = base.where(FsDedupAttempt.merged_at.isnot(None))
    # status == "all" → без дополнительного фильтра.

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    page_stmt = (
        base.order_by(FsDedupAttempt.score.desc(), FsDedupAttempt.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(page_stmt)).scalars().all()

    return FsDedupAttemptListResponse(
        tree_id=tree_id,
        status=status_q,
        total=int(total),
        limit=limit,
        offset=offset,
        items=[_to_out(r) for r in rows],
    )


@router.post(
    "/dedup-attempts/{attempt_id}/reject",
    response_model=FsDedupAttemptOut,
    tags=["dedup-attempts"],
    summary="Reject a dedup attempt (sets rejected_at).",
)
async def reject_fs_dedup_attempt(
    attempt_id: uuid.UUID,
    request: FsDedupAttemptRejectRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FsDedupAttemptOut:
    """Пометить attempt отказанным.

    Уже rejected → 200 + idempotent (без перезаписи timestamp'а).
    Уже merged → 409 (нельзя отказать после merge'а).
    """
    row = await _load_attempt_or_404(session, attempt_id)
    if row.merged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Attempt is already merged; cannot reject.",
        )
    if row.rejected_at is None:
        row.rejected_at = dt.datetime.now(dt.UTC)
        if request.reason:
            # Provenance — append-only, не пересоздаём существующие ключи.
            prov = dict(row.provenance or {})
            prov["reject_reason"] = request.reason
            row.provenance = prov
        await session.flush()
    return _to_out(row)


@router.post(
    "/dedup-attempts/{attempt_id}/merge",
    response_model=FsDedupAttemptMergeResponse,
    tags=["dedup-attempts"],
    summary="Mark a dedup attempt as merged + return Phase 4.6 merge URL.",
)
async def merge_fs_dedup_attempt(
    attempt_id: uuid.UUID,
    request: FsDedupAttemptMergeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FsDedupAttemptMergeResponse:
    """Пометить attempt как merged.

    Сам merge не выполняется здесь — UI следующим шагом идёт на
    Phase 4.6 ``POST /persons/{fs_person_id}/merge`` с ``target_id =
    candidate_person_id``. Backend только проставляет ``merged_at`` на
    attempt-row, чтобы после успешного merge у нас было consistency
    между attempt-историей и merge-историей.

    Уже merged → 200 + idempotent. Уже rejected → 409.
    """
    if request.confirm is not True:
        # Pydantic Literal[True] это ловит на validation, но guard
        # дублирующий — на случай ручного bypass'а через model_construct.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm must be true",
        )
    row = await _load_attempt_or_404(session, attempt_id)
    if row.rejected_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Attempt is already rejected; cannot merge.",
        )
    if row.merged_at is None:
        row.merged_at = dt.datetime.now(dt.UTC)
        await session.flush()
    return FsDedupAttemptMergeResponse(
        attempt_id=row.id,
        fs_person_id=row.fs_person_id,
        candidate_person_id=row.candidate_person_id,
        merged_at=row.merged_at,
        merge_url=f"/persons/{row.fs_person_id}/merge",
    )


async def _load_attempt_or_404(session: AsyncSession, attempt_id: uuid.UUID) -> FsDedupAttempt:
    """SELECT attempt by id или 404."""
    row = (
        await session.execute(select(FsDedupAttempt).where(FsDedupAttempt.id == attempt_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FsDedupAttempt {attempt_id} not found",
        )
    return row
