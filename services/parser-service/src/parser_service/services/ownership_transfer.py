"""Atomic ownership-swap helper для Tree (Phase 4.11c, ADR-0050).

Извлечён из ``api/sharing.py:transfer_owner`` (Phase 11.1) чтобы оба
flow'а — manual user-инициируемый PATCH /trees/{id}/transfer-owner и
async auto-transfer worker (этот PR) — использовали одну и ту же
проверенную атомарную процедуру.

Контракт ``swap_tree_owner_atomic``:

* Caller гарантирует, что обе membership-row уже существуют (current
  owner с role=owner, target user с role=editor|viewer и
  ``revoked_at IS NULL``).
* Если current owner-row отсутствует, helper создаёт её на лету
  (legacy-trees без явной membership — owner_user_id указывает на
  user'а, но membership-row не было backfill'ено).
* Партиал-уникальный индекс ``uq_tree_memberships_one_owner_per_tree``
  (Phase 11.0 миграция 0015) гарантирует ровно один OWNER на дерево;
  внутри одной транзакции index'ы консистентны на commit'е, не
  пошагово, так что промежуточный шаг с нулём OWNER'ов — допустим.

Auth/permissions — на caller'е (HTTP-эндпоинт делает ``require_tree_role``,
worker делает свой preflight по audit'у). Helper только мутирует state.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from shared_models import TreeRole
from shared_models.orm import Tree, TreeMembership
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class SwapResult:
    """Output ``swap_tree_owner_atomic`` — для caller'а / тестов."""

    tree_id: uuid.UUID
    previous_owner_user_id: uuid.UUID
    new_owner_user_id: uuid.UUID
    swapped_at: dt.datetime


class TreeMembershipMissingError(LookupError):
    """Caller pre-condition failed: target user не имеет active membership."""


class TreeMissingError(LookupError):
    """Tree row не существует (race с tree-deletion)."""


async def swap_tree_owner_atomic(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    current_owner_user_id: uuid.UUID,
    new_owner_user_id: uuid.UUID,
) -> SwapResult:
    """Атомарно поменять OWNER дерева на ``new_owner_user_id``.

    Шаги (в одной session, caller commit'ит):

    1. Найти target ``TreeMembership`` (tree_id + new_owner_user_id +
       revoked_at IS NULL). Если нет — поднять
       :class:`TreeMembershipMissingError`.
    2. Найти current owner ``TreeMembership``. Если нет (legacy-tree
       без явной row) — создать на лету.
    3. Загрузить ``Tree`` row. Если нет — :class:`TreeMissingError`.
    4. Демоут owner-row → editor; промоут target-row → owner;
       обновить ``trees.owner_user_id``. Flush в той же session.

    Возвращает :class:`SwapResult` для audit/log нужд caller'а.
    """
    target_membership = await session.scalar(
        select(TreeMembership).where(
            TreeMembership.tree_id == tree_id,
            TreeMembership.user_id == new_owner_user_id,
            TreeMembership.revoked_at.is_(None),
        )
    )
    if target_membership is None:
        msg = (
            f"User {new_owner_user_id} has no active membership on tree {tree_id}; "
            "invite or restore them first."
        )
        raise TreeMembershipMissingError(msg)

    owner_membership = await session.scalar(
        select(TreeMembership).where(
            TreeMembership.tree_id == tree_id,
            TreeMembership.user_id == current_owner_user_id,
            TreeMembership.role == TreeRole.OWNER.value,
            TreeMembership.revoked_at.is_(None),
        )
    )
    if owner_membership is None:
        # Legacy-tree: trees.owner_user_id указывает на user, но membership
        # row нет (или была revoked). Создаём.
        owner_membership = TreeMembership(
            tree_id=tree_id,
            user_id=current_owner_user_id,
            role=TreeRole.OWNER.value,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(owner_membership)
        await session.flush()

    tree = await session.get(Tree, tree_id)
    if tree is None:
        msg = f"Tree {tree_id} not found"
        raise TreeMissingError(msg)

    now = dt.datetime.now(dt.UTC)
    owner_membership.role = TreeRole.EDITOR.value
    target_membership.role = TreeRole.OWNER.value
    tree.owner_user_id = new_owner_user_id
    await session.flush()

    return SwapResult(
        tree_id=tree_id,
        previous_owner_user_id=current_owner_user_id,
        new_owner_user_id=new_owner_user_id,
        swapped_at=now,
    )


__all__ = [
    "SwapResult",
    "TreeMembershipMissingError",
    "TreeMissingError",
    "swap_tree_owner_atomic",
]
