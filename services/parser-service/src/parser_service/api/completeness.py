"""Completeness Assertions endpoint (Phase 15.11a / ADR-0076,
Phase 15.11b validation layer / ADR-0077).

CRUD над «sealed sets» — owner-asserted-negation flag'ами на scope вокруг
анкорной персоны. Foundation only — consumers (15.3 / 15.5 / 15.6 / 10.7)
интегрируются в 15.11c, UI — в 15.11d.

Routes:

* ``POST   /trees/{tree_id}/persons/{person_id}/completeness``
  — create or upsert assertion (для (tree, person, scope) активна
  ровно одна). Source list заменяется атомарно. Body: scope, is_sealed,
  note?, source_ids[], override?.
* ``GET    /trees/{tree_id}/persons/{person_id}/completeness``
  — list active assertions для персоны (eager-load sources).
* ``GET    /trees/{tree_id}/persons/{person_id}/completeness/{scope}``
  — single по scope, 404 если нет.
* ``DELETE /trees/{tree_id}/persons/{person_id}/completeness/{scope}``
  — *revoke* (sets is_sealed=False, чистит junction, KEEPS row).
  Soft-delete row'и оставляем для GDPR-purge / hard-cleanup.

Permission gate: VIEWER+ на read, EDITOR+ на write/revoke (как в safe_merge).

Validation (Phase 15.11b):
    * is_sealed=True требует ≥1 source → 422
    * source_ids должны быть live и принадлежать тому же tree → 422
    * re-assert другим user'ом без override=True → 409
    * override=True пишет audit-row с metadata
    * revoke пишет audit-row
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from shared_models import TreeRole
from shared_models.enums import AuditAction, CompletenessScope
from shared_models.orm import (
    CompletenessAssertion,
    CompletenessAssertionSource,
    Person,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parser_service.auth import get_current_user_id
from parser_service.completeness import (
    emit_completeness_audit,
    validate_assertion_create,
    validate_assertion_revoke,
)
from parser_service.database import get_session
from parser_service.services.permissions import require_tree_role

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CompletenessAssertionCreate(BaseModel):
    """Body of POST /trees/{tree_id}/persons/{person_id}/completeness.

    ``override`` (Phase 15.11b): caller подтверждает, что осведомлён о
    существующей active assertion от другого user'а и сознательно её
    перезаписывает. Без override — re-assertion другим user'ом отклоняется
    с 409.
    """

    model_config = ConfigDict(extra="forbid")

    scope: CompletenessScope
    is_sealed: bool = True
    note: str | None = Field(default=None, max_length=2000)
    source_ids: list[uuid.UUID] = Field(default_factory=list)
    override: bool = False


class CompletenessAssertionRead(BaseModel):
    """Response shape: assertion + linked source ids."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    tree_id: uuid.UUID
    subject_person_id: uuid.UUID
    scope: CompletenessScope
    is_sealed: bool
    note: str | None
    asserted_by: uuid.UUID | None
    source_ids: list[uuid.UUID]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_read(row: CompletenessAssertion) -> CompletenessAssertionRead:
    """ORM → Pydantic. Source ids собираются из eager-loaded junction."""
    return CompletenessAssertionRead(
        id=row.id,
        tree_id=row.tree_id,
        subject_person_id=row.subject_person_id,
        scope=CompletenessScope(row.scope),
        is_sealed=row.is_sealed,
        note=row.note,
        asserted_by=row.asserted_by,
        source_ids=[link.source_id for link in row.sources],
    )


async def _ensure_person_in_tree(
    session: AsyncSession, tree_id: uuid.UUID, person_id: uuid.UUID
) -> None:
    """404 если person не найден или не принадлежит tree_id."""
    result = await session.execute(
        select(Person.id).where(
            Person.id == person_id,
            Person.tree_id == tree_id,
            Person.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person {person_id} not found in tree {tree_id}",
        )


async def _load_active(
    session: AsyncSession,
    tree_id: uuid.UUID,
    person_id: uuid.UUID,
    scope: CompletenessScope | None = None,
) -> list[CompletenessAssertion]:
    """Active rows + eager-loaded sources (одно SQL без N+1)."""
    stmt = (
        select(CompletenessAssertion)
        .where(
            CompletenessAssertion.tree_id == tree_id,
            CompletenessAssertion.subject_person_id == person_id,
            CompletenessAssertion.deleted_at.is_(None),
        )
        .options(selectinload(CompletenessAssertion.sources))
    )
    if scope is not None:
        stmt = stmt.where(CompletenessAssertion.scope == scope.value)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/trees/{tree_id}/persons/{person_id}/completeness",
    response_model=CompletenessAssertionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create or upsert completeness assertion for a person scope.",
    dependencies=[Depends(require_tree_role(TreeRole.EDITOR))],
)
async def create_assertion(
    tree_id: uuid.UUID,
    person_id: uuid.UUID,
    payload: CompletenessAssertionCreate,
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompletenessAssertionRead:
    """Создать или upsert'нуть active assertion для (tree, person, scope).

    Если active row для тройки уже есть — атомарно заменяем sources и
    обновляем is_sealed/note. Invariant «одна active assertion на
    scope per person» — ADR-0076 §Schema.

    Validation (Phase 15.11b / ADR-0077):
      * is_sealed=True без ``source_ids`` → 422.
      * source_ids указывают на live, same-tree sources → иначе 422.
      * existing assertion от другого user'а без ``override=True`` → 409.
      * override=True пишет audit-row с prev_actor + override metadata.
    """
    await _ensure_person_in_tree(session, tree_id, person_id)

    ctx = await validate_assertion_create(
        session,
        tree_id=tree_id,
        subject_person_id=person_id,
        scope=payload.scope,
        is_sealed=payload.is_sealed,
        source_ids=payload.source_ids,
        actor_user_id=user_id,
        override=payload.override,
    )

    if ctx.existing is not None:
        row = ctx.existing
        row.is_sealed = payload.is_sealed
        row.note = payload.note
        row.asserted_by = user_id
        # Атомарная замена sources: удаляем все junction-rows, заводим новые.
        # ``cascade="all, delete-orphan"`` на relationship чистит старые при
        # очистке коллекции.
        row.sources.clear()
        await session.flush()
    else:
        row = CompletenessAssertion(
            tree_id=tree_id,
            subject_person_id=person_id,
            scope=payload.scope.value,
            is_sealed=payload.is_sealed,
            note=payload.note,
            asserted_by=user_id,
        )
        session.add(row)
        await session.flush()

    for source_id in payload.source_ids:
        session.add(
            CompletenessAssertionSource(
                assertion_id=row.id,
                source_id=source_id,
            )
        )
    await session.flush()

    # Override audit — пишется ТОЛЬКО когда existing assertion от другого
    # user'а перезаписывается. Auto-listener зафиксирует `UPDATE diff`
    # отдельно; этот row добавляет ``reason`` + override metadata.
    if ctx.is_override_reassertion:
        emit_completeness_audit(
            session,
            tree_id=tree_id,
            assertion_id=row.id,
            actor_user_id=user_id,
            action=AuditAction.UPDATE,
            reason="override_reassertion",
            diff={
                "scope": payload.scope.value,
                "subject_person_id": str(person_id),
                "prev_actor_id": (
                    str(ctx.prev_actor_id) if ctx.prev_actor_id is not None else None
                ),
                "new_actor_id": str(user_id),
                "is_sealed": payload.is_sealed,
                "source_count": len(payload.source_ids),
            },
        )
        await session.flush()

    await session.refresh(row, attribute_names=["sources"])
    await session.commit()
    return _to_read(row)


@router.get(
    "/trees/{tree_id}/persons/{person_id}/completeness",
    response_model=list[CompletenessAssertionRead],
    summary="List active completeness assertions for a person.",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def list_assertions(
    tree_id: uuid.UUID,
    person_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CompletenessAssertionRead]:
    """Все active assertion'ы для персоны, eager-loaded sources."""
    await _ensure_person_in_tree(session, tree_id, person_id)
    rows = await _load_active(session, tree_id, person_id)
    return [_to_read(r) for r in rows]


@router.get(
    "/trees/{tree_id}/persons/{person_id}/completeness/{scope}",
    response_model=CompletenessAssertionRead,
    summary="Get a single completeness assertion by scope.",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_assertion(
    tree_id: uuid.UUID,
    person_id: uuid.UUID,
    scope: CompletenessScope,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CompletenessAssertionRead:
    """Active assertion для (tree, person, scope) или 404."""
    await _ensure_person_in_tree(session, tree_id, person_id)
    rows = await _load_active(session, tree_id, person_id, scope)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active completeness assertion for scope {scope.value}",
        )
    return _to_read(rows[0])


@router.delete(
    "/trees/{tree_id}/persons/{person_id}/completeness/{scope}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke (unseal) a completeness assertion; KEEPS row for audit.",
    dependencies=[Depends(require_tree_role(TreeRole.EDITOR))],
)
async def revoke_assertion(
    tree_id: uuid.UUID,
    person_id: uuid.UUID,
    scope: CompletenessScope,
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Revoke: ``is_sealed=False`` + clear sources, row остаётся.

    NB: row не получает ``deleted_at`` — soft-delete оставлен для других
    flow'ов (GDPR-purge, owner-инициированный hard cleanup). Семантика
    revoke'а (read-side: «эта семья снова открыта») реализуется через
    ``is_sealed`` flag, а не через soft-delete.

    Phase 15.11b: revoke пишет audit-row с reason=``revoke`` и metadata
    о prev_actor — пусть downstream consumer'ы видят, кто и когда
    инициировал unseal.
    """
    await _ensure_person_in_tree(session, tree_id, person_id)
    ctx = await validate_assertion_revoke(
        session,
        tree_id=tree_id,
        subject_person_id=person_id,
        scope=scope,
        actor_user_id=user_id,
    )
    row = ctx.existing
    row.is_sealed = False
    row.sources.clear()
    await session.flush()

    emit_completeness_audit(
        session,
        tree_id=tree_id,
        assertion_id=row.id,
        actor_user_id=user_id,
        action=AuditAction.DELETE,
        reason="revoke",
        diff={
            "scope": scope.value,
            "subject_person_id": str(person_id),
            "prev_actor_id": (str(ctx.prev_actor_id) if ctx.prev_actor_id is not None else None),
            "revoking_actor_id": str(user_id),
        },
    )
    await session.flush()
    await session.commit()
