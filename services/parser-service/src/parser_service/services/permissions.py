"""Phase 11.0 — pure-функции и FastAPI-зависимости для row-level доступа.

См. ADR-0036 «Sharing & permissions model».

Контракт:

* :func:`check_tree_permission` — async pure-функция, без побочных эффектов
  кроме чтения из ``session``. Возвращает ``True``/``False``. Используется
  call-site'ами, где нужен явный if/then (например, partial-rendering UI'я
  или multi-tree dashboard).
* :func:`require_tree_role` — фабрика FastAPI-зависимости. Принимает минимально
  требуемую :class:`TreeRole`, возвращает зависимость, которая 403'ит, если
  user не satisfies требование. Использование::

      @router.delete("/trees/{tree_id}", status_code=204)
      async def delete_tree(
          tree_id: uuid.UUID,
          _gate: None = Depends(require_tree_role(TreeRole.OWNER)),
      ) -> None: ...

* :func:`get_user_role_in_tree` — вернуть текущую активную роль (или None).

Все три используют единый запрос: один JOIN на ``tree_memberships`` по
``(tree_id, user_id)`` с фильтром ``revoked_at IS NULL``. Phase 11.0
не оптимизирует через кэш — staging-нагрузка не требует. Когда станет
узким местом, кэш кладётся в ``request.state`` (один lookup на запрос).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from shared_models import TreeRole, role_satisfies
from shared_models.orm import Person, Tree, TreeMembership, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import get_current_user
from parser_service.database import get_session


async def get_user_role_in_tree(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tree_id: uuid.UUID,
) -> str | None:
    """Вернуть строковое значение активной роли пользователя в дереве.

    «Активная» = ``revoked_at IS NULL``. Резолвится по двум источникам:

    1. ``tree_memberships`` — основной источник правды для всех ролей.
    2. **Fallback на ``trees.owner_user_id``** — если membership-row отсутствует,
       а ``user_id == tree.owner_user_id``, считаем OWNER. Зачем: до Phase 11.1
       создание дерева через import-job / FS-importer / прямой ORM-insert НЕ
       пишет membership-row; backfill в миграции 0015 покрывает только trees,
       существовавшие на момент применения, но не новые. Fallback гарантирует
       что владелец не получает 403 на собственное только-что-созданное дерево.
       После Phase 11.1 (явный create-tree flow с membership-insert) этот
       fallback можно убрать.

    Если ни один источник не даёт роль — ``None`` (fail-closed).
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

    # Fallback: проверяем trees.owner_user_id напрямую.
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
    """Возвращает True, если ``user`` имеет ≥ ``required`` роль в ``tree``.

    Иерархия: OWNER ⊃ EDITOR ⊃ VIEWER. Несуществующее членство → False
    (fail-closed).
    """
    role = await get_user_role_in_tree(session, user_id=user_id, tree_id=tree_id)
    if role is None:
        return False
    return role_satisfies(role, required)


def require_tree_role(required: TreeRole) -> object:
    """Фабрика FastAPI-зависимости — 403 если user не имеет ``required`` роль в ``tree_id``.

    Принимает ``tree_id: UUID`` из path-параметра (через :class:`fastapi.Path`)
    — call-site'у в роутере не нужно ничего добавлять, кроме самой зависимости.

    Возвращает зависимость, которая ничего полезного не отдаёт (``None``);
    единственный смысл — отвалиться раньше, чем дойдём до тела роута.

    Пример::

        @router.delete(
            "/trees/{tree_id}",
            dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
        )
        async def delete_tree(tree_id: uuid.UUID) -> None: ...
    """

    async def _gate(
        tree_id: Annotated[uuid.UUID, Path(...)],
        session: Annotated[AsyncSession, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> None:
        # 404 vs 403: если дерево не существует — 404 (старая семантика
        # endpoint'ов сохраняется, существующие тесты не ломаются). Если
        # существует, но user не имеет роли — 403.
        tree_exists = await session.scalar(select(Tree.id).where(Tree.id == tree_id))
        if tree_exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tree {tree_id} not found",
            )
        ok = await check_tree_permission(
            session,
            user_id=user.id,
            tree_id=tree_id,
            required=required,
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(f"User {user.id} does not have {required.value} access on tree {tree_id}"),
            )

    return _gate


def require_person_tree_role(required: TreeRole) -> object:
    """Gate-зависимость для роутов с ``person_id`` в path вместо ``tree_id``.

    Резолвит ``Person → Tree`` (один SQL-запрос) и проверяет роль. Используется
    в persons-merge, hypothesis-mutate и других per-person эндпоинтах, где
    дерево не в URL'е.

    Если ``Person`` не существует или soft-deleted — 404 (а не 403): это
    оригинальная ошибка ресурса, не privacy-leak.
    """

    async def _gate(
        person_id: Annotated[uuid.UUID, Path(...)],
        session: Annotated[AsyncSession, Depends(get_session)],
        user: Annotated[User, Depends(get_current_user)],
    ) -> None:
        tree_id = await session.scalar(
            select(Person.tree_id).where(
                Person.id == person_id,
                Person.deleted_at.is_(None),
            )
        )
        if tree_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Person {person_id} not found",
            )
        ok = await check_tree_permission(
            session,
            user_id=user.id,
            tree_id=tree_id,
            required=required,
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(f"User {user.id} does not have {required.value} access on tree {tree_id}"),
            )

    return _gate
