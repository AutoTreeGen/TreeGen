"""Phase 6.4 — row-level permission gate для dna-service (зеркало parser-service).

Зачем дубль, а не общий модуль в ``shared_models``: dna-service использует
свой собственный ``RequireUser`` (резолвит ``users.id`` напрямую через
``clerk_user_id``, без JIT-create), и FastAPI-зависимости с ``get_session``
естественно живут в самом сервисе. Pattern идентичен, контракт identical;
если в будущем добавится третий сервис с тем же gate'ом — извлечём в
``shared_models.permissions`` через одну общую async pure-функцию + per-service
DI-обёртку.

См. ADR-0036 «Sharing & permissions model» для семантики ролей и
ADR-0054 §«Permission gate» для контекста Phase 6.4.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from shared_models import TreeRole, role_satisfies
from shared_models.orm import Tree, TreeMembership
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.auth import RequireUser
from dna_service.database import get_session


async def get_user_role_in_tree(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
) -> str | None:
    """Активная роль user'а в дереве.

    Резолвит из двух источников:

    1. ``tree_memberships`` (revoked_at IS NULL) — основной канал для всех ролей.
    2. Fallback на ``trees.owner_user_id`` — для деревьев, созданных до
       Phase 11.0 backfill-миграции 0015 / dev-flow без membership-row.

    Если ни один источник не отдал роль — ``None`` (fail-closed).
    """
    res = await session.execute(
        select(TreeMembership.role).where(
            TreeMembership.tree_id == tree_id,
            TreeMembership.user_id == user_id,
            TreeMembership.revoked_at.is_(None),
        )
    )
    role = res.scalar_one_or_none()
    if role is not None:
        return role

    owner_id = await session.scalar(select(Tree.owner_user_id).where(Tree.id == tree_id))
    if owner_id is not None and owner_id == user_id:
        return TreeRole.OWNER.value
    return None


async def check_tree_permission(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
    required: TreeRole,
) -> bool:
    """``True`` если ``user_id`` имеет ≥ ``required`` роль в дереве."""
    role = await get_user_role_in_tree(session, user_id=user_id, tree_id=tree_id)
    if role is None:
        return False
    return role_satisfies(role, required)


def require_tree_role(required: TreeRole) -> object:
    """FastAPI dep-factory: 403 если у caller'а нет ``required`` роли в ``tree_id``-path.

    404 если дерево не существует — privacy-safe (не выдаём существование
    чужих деревьев).

    Использование::

        @router.get(
            "/trees/{tree_id}/triangulation",
            dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
        )
        async def get_triangulation(tree_id: uuid.UUID, ...) -> ...
    """

    async def _gate(
        user_id: RequireUser,
        tree_id: Annotated[uuid.UUID, Path(...)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        tree_exists = await session.scalar(select(Tree.id).where(Tree.id == tree_id))
        if tree_exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tree {tree_id} not found",
            )
        ok = await check_tree_permission(
            session,
            user_id=user_id,
            tree_id=tree_id,
            required=required,
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(f"User does not have {required.value} access on tree {tree_id}"),
            )

    return _gate


__all__ = [
    "check_tree_permission",
    "get_user_role_in_tree",
    "require_tree_role",
]
