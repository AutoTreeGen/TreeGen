"""Completeness assertions validation layer (Phase 15.11b / ADR-0077).

Заменяет permissive CRUD из 15.11a на enforcement-слой:

1. ``is_sealed=True`` требует ≥1 source в той же транзакции (422).
2. Каждый ``source_id`` должен ссылаться на live-source того же дерева
   (422 на cross-tree, 422 на soft-deleted).
3. Re-assertion existing seal другим пользователем требует ``override=True``
   и пишет audit-row (409 без override).
4. Revoke (DELETE) пишет audit-row.

Role-gate (EDITOR+) уже навешен на POST/DELETE через
``require_tree_role`` — этот модуль не дублирует его, а валидирует
данные внутри уже допущенного запроса. См. ADR-0077 §«Role gate».

Audit-row пишется поверх auto-listener'а из ``shared_models.audit``:
listener фиксирует «UPDATE на assertion с такими-то diff'ами»,
а наш override/revoke audit добавляет ``reason`` (override_reassertion /
revoke) с overrider/prev_actor metadata в ``diff``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, Final

from fastapi import HTTPException, status
from shared_models.enums import ActorKind, AuditAction, CompletenessScope
from shared_models.orm import (
    AuditLog,
    CompletenessAssertion,
    Source,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Entity-type, под которым audit-rows для completeness-assertion'ов
# регистрируются в audit_log.entity_type. Соответствует
# ``CompletenessAssertion.__tablename__``.
_AUDIT_ENTITY_TYPE: Final = "completeness_assertions"

# ``reason``-маркеры для manual audit-rows. Auto-listener пишет с
# ``reason=NULL``, поэтому фильтр по reason различает «system-emitted
# UPDATE diff» и «override/revoke event с metadata».
_REASON_OVERRIDE: Final = "override_reassertion"
_REASON_REVOKE: Final = "revoke"


# ---------------------------------------------------------------------------
# Exceptions — все наследуют HTTPException, чтобы FastAPI рендерил статус.
# ---------------------------------------------------------------------------


class ValidationError(HTTPException):
    """Базовый класс для всех validation-ошибок 15.11b."""


class SourceRequiredError(ValidationError):
    """``is_sealed=True`` при пустом ``source_ids`` (422)."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A sealed assertion requires at least one source citation.",
        )


class SourceNotFoundError(ValidationError):
    """``source_id`` не существует (422)."""

    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Source {source_id} not found.",
        )


class SourceCrossTreeError(ValidationError):
    """``source_id`` принадлежит другому дереву (422).

    Проверка обязательна — без неё caller мог бы «протащить» source из
    чужого дерева, к которому имеет доступ, и тем самым обойти
    privacy-границу tree-сегрегации.
    """

    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Source {source_id} does not belong to this tree.",
        )


class SourceDeletedError(ValidationError):
    """``source_id`` указывает на soft-deleted source (422)."""

    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Source {source_id} has been deleted.",
        )


class OverrideRequiredError(ValidationError):
    """Re-assertion другим пользователем без ``override=True`` (409)."""

    def __init__(self, prev_actor_id: uuid.UUID | None) -> None:
        prev = str(prev_actor_id) if prev_actor_id is not None else "unknown"
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Active assertion was made by user {prev}; "
                "pass override=true to re-assert as a different user."
            ),
        )


# ---------------------------------------------------------------------------
# Context-объекты, возвращаемые validate_*. Caller использует их, чтобы
# знать, что именно делать с БД и какой audit-row эмитить.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssertionUpsertContext:
    """Результат validate_assertion_create.

    ``existing`` is None — это insert; not None — upsert.
    ``is_override_reassertion`` True означает «существующая active row
    создана другим user'ом, override=True был передан»; caller обязан
    вызвать :func:`emit_completeness_audit` после успешного flush'а.
    """

    existing: CompletenessAssertion | None
    is_override_reassertion: bool
    prev_actor_id: uuid.UUID | None


@dataclass(frozen=True)
class AssertionRevokeContext:
    """Результат validate_assertion_revoke. ``existing`` всегда не None."""

    existing: CompletenessAssertion
    prev_actor_id: uuid.UUID | None


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


async def validate_assertion_create(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    subject_person_id: uuid.UUID,
    scope: CompletenessScope,
    is_sealed: bool,
    source_ids: list[uuid.UUID],
    actor_user_id: uuid.UUID,
    override: bool = False,
) -> AssertionUpsertContext:
    """Валидировать payload POST /completeness; raise при нарушениях.

    Контракт:
      * 422 :class:`SourceRequiredError` — sealed без sources.
      * 422 :class:`SourceNotFoundError` — source_id не существует.
      * 422 :class:`SourceCrossTreeError` — source принадлежит другому дереву.
      * 422 :class:`SourceDeletedError` — source soft-deleted.
      * 409 :class:`OverrideRequiredError` — re-assert другим user'ом без override.

    Возвращает :class:`AssertionUpsertContext` для caller'а: insert vs
    upsert vs override-upsert + prev_actor для audit metadata.
    """
    # 1. Source-required для sealed assertion.
    if is_sealed and not source_ids:
        raise SourceRequiredError()

    # 2. Все source_ids должны быть live + same-tree. Один SQL для всего set'а
    # (проще диагностировать): тащим row'ы и сверяем counts.
    if source_ids:
        await _validate_source_ids(session, tree_id=tree_id, source_ids=source_ids)

    # 3. Existing-row check для override-mechanic.
    existing = await _load_existing_active(
        session,
        tree_id=tree_id,
        subject_person_id=subject_person_id,
        scope=scope,
    )

    is_override = False
    prev_actor: uuid.UUID | None = None
    if existing is not None:
        prev_actor = existing.asserted_by
        if prev_actor is not None and prev_actor != actor_user_id:
            if not override:
                raise OverrideRequiredError(prev_actor)
            is_override = True

    return AssertionUpsertContext(
        existing=existing,
        is_override_reassertion=is_override,
        prev_actor_id=prev_actor,
    )


async def validate_assertion_revoke(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    subject_person_id: uuid.UUID,
    scope: CompletenessScope,
    actor_user_id: uuid.UUID,
) -> AssertionRevokeContext:
    """Валидировать DELETE /completeness/{scope}.

    Caller должен на основе ``existing`` row выполнить unseal + clear
    sources + flush + ``emit_completeness_audit(action=DELETE,
    reason=_REASON_REVOKE, ...)``.

    404 поднимается caller'ом до вызова этой функции (через
    ``_load_active``); здесь lookup выполняется только чтобы вытащить
    prev_actor для audit-metadata. Это держит handler-side error pathways
    единообразными с 15.11a's revoke (404 для no-row).

    ``actor_user_id`` принимается для симметрии с create-валидатором, но
    revoke не зависит от роли asserted_by (любой EDITOR+ может revoke).
    """
    # actor_user_id: используется через caller для emit_completeness_audit;
    # здесь не валидируем roles (framework-level gate уже проверил EDITOR+).
    del actor_user_id
    existing = await _load_existing_active(
        session,
        tree_id=tree_id,
        subject_person_id=subject_person_id,
        scope=scope,
    )
    if existing is None:
        # Caller тоже проверит и поднимет 404; double-check здесь — defensive.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active completeness assertion for scope {scope.value}",
        )
    return AssertionRevokeContext(existing=existing, prev_actor_id=existing.asserted_by)


# ---------------------------------------------------------------------------
# Audit emission helper
# ---------------------------------------------------------------------------


def emit_completeness_audit(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    assertion_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: AuditAction,
    reason: str,
    diff: dict[str, Any],
) -> None:
    """Записать manual audit-row для completeness-event'а.

    Параллельно auto-listener пишет UPDATE/DELETE diff'ы для самой
    ``CompletenessAssertion``-row'ы; этот helper эмитит дополнительный
    row с ``reason``-маркером и event-specific metadata в ``diff``
    (override metadata, revoke trigger, etc).

    ``actor_kind=USER`` — completeness-event'ы всегда инициированы
    явным user-action'ом; в отличие от GDPR-actions, system-actor
    здесь не появляется.
    """
    session.add(
        AuditLog(
            tree_id=tree_id,
            entity_type=_AUDIT_ENTITY_TYPE,
            entity_id=assertion_id,
            action=action.value,
            actor_user_id=actor_user_id,
            actor_kind=ActorKind.USER.value,
            reason=reason,
            diff=diff,
            created_at=dt.datetime.now(dt.UTC),
        )
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _validate_source_ids(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    source_ids: list[uuid.UUID],
) -> None:
    """Проверить, что все source_ids существуют, принадлежат tree, не deleted."""
    # Тащим всё за один SQL — including soft-deleted, чтобы различать
    # "не существует" от "был удалён".
    result = await session.execute(
        select(Source.id, Source.tree_id, Source.deleted_at).where(Source.id.in_(source_ids))
    )
    rows = {row.id: (row.tree_id, row.deleted_at) for row in result.all()}

    for sid in source_ids:
        if sid not in rows:
            raise SourceNotFoundError(sid)
        src_tree_id, deleted_at = rows[sid]
        if src_tree_id != tree_id:
            raise SourceCrossTreeError(sid)
        if deleted_at is not None:
            raise SourceDeletedError(sid)


async def _load_existing_active(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    subject_person_id: uuid.UUID,
    scope: CompletenessScope,
) -> CompletenessAssertion | None:
    """Active row для (tree, person, scope) с eager-loaded sources, или None."""
    result = await session.execute(
        select(CompletenessAssertion)
        .where(
            CompletenessAssertion.tree_id == tree_id,
            CompletenessAssertion.subject_person_id == subject_person_id,
            CompletenessAssertion.scope == scope.value,
            CompletenessAssertion.deleted_at.is_(None),
        )
        .options(selectinload(CompletenessAssertion.sources))
    )
    return result.scalar_one_or_none()
