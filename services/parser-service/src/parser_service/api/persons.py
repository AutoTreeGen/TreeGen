"""Persons API: manual merge flow (Phase 4.6, ADR-0022).

CLAUDE.md §5: auto-merge запрещён. Все эндпоинты этого модуля
двухшаговые — preview без mutation'а, commit обязательно с
``confirm:true``, undo в окне 90 дней.

Эндпоинты:

* ``POST /persons/{person_id}/merge/preview`` — diff + hypothesis-check
  без записи в БД.
* ``POST /persons/{person_id}/merge`` — commit (требует confirm:true,
  иначе FastAPI вернёт 422; bare body без ``confirm`` — 422 как validation
  failure; явно ``confirm: false`` — 422 от Literal[True] валидатора).
* ``POST /persons/merge/{merge_id}/undo`` — откат, 410 за окном.
* ``GET /persons/{person_id}/merge-history`` — список merge'ей где
  персона участвовала.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from shared_models import TreeRole
from shared_models.orm import PersonMergeLog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.schemas import (
    MergeCommitRequest,
    MergeCommitResponse,
    MergeEventDiff,
    MergeFamilyMembershipDiff,
    MergeFieldDiff,
    MergeHistoryItem,
    MergeHistoryResponse,
    MergeHypothesisConflict,
    MergeNameDiff,
    MergePreviewResponse,
    MergeUndoResponse,
)
from parser_service.services.permissions import require_person_tree_role
from parser_service.services.person_merger import (
    MergeBlockedError,
    PersonMergerLookupError,
    UndoNotAllowedError,
    apply_merge,
    compute_diff,
    undo_merge,
)

router = APIRouter()


def _diff_to_response(diff) -> MergePreviewResponse:  # type: ignore[no-untyped-def]
    """Адаптер от ``MergeDiff`` (dataclass) к Pydantic response."""
    return MergePreviewResponse(
        survivor_id=diff.survivor_id,
        merged_id=diff.merged_id,
        default_survivor_id=diff.default_survivor_id,
        fields=[
            MergeFieldDiff(
                field=f.field,
                survivor_value=f.survivor_value,
                merged_value=f.merged_value,
                after_merge_value=f.after_merge_value,
            )
            for f in diff.fields
        ],
        names=[
            MergeNameDiff(
                name_id=n.name_id,
                old_sort_order=n.old_sort_order,
                new_sort_order=n.new_sort_order,
            )
            for n in diff.names
        ],
        events=[
            MergeEventDiff(
                event_id=e.event_id,
                action=e.action,
                collapsed_into=e.collapsed_into,
            )
            for e in diff.events
        ],
        family_memberships=[
            MergeFamilyMembershipDiff(table=fm.table, row_id=fm.row_id)
            for fm in diff.family_memberships
        ],
        hypothesis_check=diff.hypothesis_check,
        conflicts=[
            MergeHypothesisConflict(
                reason=c.reason,
                hypothesis_id=c.hypothesis_id,
                detail=c.detail,
            )
            for c in diff.conflicts
        ],
    )


@router.post(
    "/persons/{person_id}/merge/preview",
    response_model=MergePreviewResponse,
    summary="Preview a person merge — no DB writes (Phase 4.6, ADR-0022).",
    dependencies=[Depends(require_person_tree_role(TreeRole.EDITOR))],
)
async def preview_merge(
    person_id: uuid.UUID,
    payload: MergeCommitRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MergePreviewResponse:
    """Возвращает полный diff + hypothesis-check.

    Reuses ``MergeCommitRequest`` для удобства фронта (то же тело, что
    и у commit'а), но `confirm` тут не обязателен в семантическом смысле —
    проверки на стороне сервера всё равно происходят. UI может вызывать
    preview без выбора survivor — тогда вернёмся к default.
    """
    try:
        diff = await compute_diff(
            session,
            a_id=person_id,
            b_id=payload.target_id,
            survivor_choice=payload.survivor_choice,
        )
    except PersonMergerLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return _diff_to_response(diff)


@router.post(
    "/persons/{person_id}/merge",
    response_model=MergeCommitResponse,
    summary="Commit a person merge — requires confirm:true (CLAUDE.md §5).",
    dependencies=[Depends(require_person_tree_role(TreeRole.EDITOR))],
)
async def commit_merge(
    person_id: uuid.UUID,
    payload: MergeCommitRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MergeCommitResponse:
    """Транзакционный merge, идемпотентный по ``confirm_token``."""
    try:
        # Phase 11.0: ``begin_nested`` (SAVEPOINT) вместо ``begin()``, потому что
        # permission-gate ``require_person_tree_role`` уже autobegin'ил
        # транзакцию SELECT'ом по persons/tree_memberships. SAVEPOINT даёт ту
        # же атомарность для apply_merge, не конфликтуя с уже-открытой outer-tx.
        async with session.begin_nested():
            log = await apply_merge(
                session,
                a_id=person_id,
                b_id=payload.target_id,
                survivor_choice=payload.survivor_choice,
                confirm_token=payload.confirm_token,
            )
    except PersonMergerLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except MergeBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "hypothesis_conflict",
                "blocking_hypotheses": [
                    {
                        "reason": c.reason,
                        "hypothesis_id": str(c.hypothesis_id) if c.hypothesis_id else None,
                        "detail": c.detail,
                    }
                    for c in exc.conflicts
                ],
            },
        ) from exc

    return MergeCommitResponse(
        merge_id=log.id,
        survivor_id=log.survivor_id,
        merged_id=log.merged_id,
        merged_at=log.merged_at,
        confirm_token=log.confirm_token,
    )


@router.post(
    "/persons/merge/{merge_id}/undo",
    response_model=MergeUndoResponse,
    summary="Undo a person merge within the 90-day window.",
)
async def undo(
    merge_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MergeUndoResponse:
    """Откатывает merge, если в окне 90 дней и merged person ещё в БД."""
    try:
        # Phase 11.0 — см. комментарий в commit_merge о begin_nested.
        async with session.begin_nested():
            log = await undo_merge(session, merge_id=merge_id)
    except PersonMergerLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except UndoNotAllowedError as exc:
        if exc.reason in ("undo_window_expired", "merged_person_purged", "survivor_purged"):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"reason": exc.reason, "message": exc.detail},
            ) from exc
        # already_undone — конфликт состояний, не «gone».
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": exc.reason, "message": exc.detail},
        ) from exc

    assert log.undone_at is not None  # invariant after successful undo
    return MergeUndoResponse(
        merge_id=log.id,
        survivor_id=log.survivor_id,
        merged_id=log.merged_id,
        undone_at=log.undone_at,
    )


@router.get(
    "/persons/{person_id}/merge-history",
    response_model=MergeHistoryResponse,
    summary="List all merges where this person was survivor or merged.",
)
async def merge_history(
    person_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MergeHistoryResponse:
    """Возвращает все merge-логи, в которых ``person_id`` участвовал."""
    res = await session.execute(
        select(PersonMergeLog)
        .where(
            or_(
                PersonMergeLog.survivor_id == person_id,
                PersonMergeLog.merged_id == person_id,
            )
        )
        .order_by(PersonMergeLog.merged_at.desc())
    )
    items = [
        MergeHistoryItem(
            merge_id=log.id,
            survivor_id=log.survivor_id,
            merged_id=log.merged_id,
            merged_at=log.merged_at,
            undone_at=log.undone_at,
            purged_at=log.purged_at,
        )
        for log in res.scalars().all()
    ]
    return MergeHistoryResponse(person_id=person_id, items=items)
