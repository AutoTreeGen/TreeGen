"""Юнит-тесты ``parser_service.services.permissions`` (Phase 11.0).

Покрывают:

* ``role_satisfies`` — полная матрица OWNER × EDITOR × VIEWER × required.
* ``check_tree_permission`` — реальная DB через testcontainers, все комбинации.
* Fallback на ``trees.owner_user_id`` когда membership-row отсутствует.

Маркеры: ``db`` + ``integration`` — требуют testcontainers Postgres.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from parser_service.services.permissions import (
    check_tree_permission,
    get_user_role_in_tree,
)
from shared_models import TreeRole, role_satisfies
from shared_models.orm import Tree, TreeMembership, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Pure unit — role_satisfies (без DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "required", "expected"),
    [
        # OWNER ⊃ EDITOR ⊃ VIEWER
        (TreeRole.OWNER, TreeRole.OWNER, True),
        (TreeRole.OWNER, TreeRole.EDITOR, True),
        (TreeRole.OWNER, TreeRole.VIEWER, True),
        (TreeRole.EDITOR, TreeRole.OWNER, False),
        (TreeRole.EDITOR, TreeRole.EDITOR, True),
        (TreeRole.EDITOR, TreeRole.VIEWER, True),
        (TreeRole.VIEWER, TreeRole.OWNER, False),
        (TreeRole.VIEWER, TreeRole.EDITOR, False),
        (TreeRole.VIEWER, TreeRole.VIEWER, True),
    ],
)
def test_role_satisfies_matrix(actual: TreeRole, required: TreeRole, expected: bool) -> None:
    """Полная матрица: каждая роль удовлетворяет себе и более слабым."""
    assert role_satisfies(actual, required) is expected


def test_role_satisfies_accepts_strings_from_db() -> None:
    """``role_satisfies`` принимает str (DB-формат) на ``actual`` тоже."""
    assert role_satisfies("owner", TreeRole.EDITOR) is True
    assert role_satisfies("viewer", TreeRole.EDITOR) is False


def test_role_satisfies_unknown_value_fail_closed() -> None:
    """Неизвестная роль (опечатка / новая роль не в шкале) → False."""
    assert role_satisfies("admin", TreeRole.VIEWER) is False
    assert role_satisfies(TreeRole.OWNER, "admin") is False


# ---------------------------------------------------------------------------
# DB-backed — check_tree_permission и get_user_role_in_tree
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str):
    """Async sessionmaker против test-postgres."""
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: async_sessionmaker[Any], *, email: str | None = None) -> User:
    """Создать User в БД."""
    e = email or f"perm-{uuid.uuid4().hex[:8]}@example.com"
    async with factory() as session:
        user = User(
            email=e,
            external_auth_id=f"local:{e}",
            display_name=e.split("@", 1)[0],
            locale="en",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_tree(factory: async_sessionmaker[Any], *, owner: User) -> Tree:
    """Создать Tree, owner — переданный User (без явного membership-row)."""
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Perm Test {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.commit()
        await session.refresh(tree)
        return tree


async def _add_membership(
    factory: async_sessionmaker[Any],
    *,
    tree: Tree,
    user: User,
    role: TreeRole,
) -> TreeMembership:
    """Создать active membership row."""
    async with factory() as session:
        m = TreeMembership(
            tree_id=tree.id,
            user_id=user.id,
            role=role.value,
            accepted_at=None,
        )
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m


@pytest.mark.asyncio
async def test_owner_via_membership_row(session_factory) -> None:
    """OWNER через явный membership-row проходит все required-уровни."""
    owner = await _make_user(session_factory)
    other = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=other)  # tree.owner_user_id != owner
    await _add_membership(session_factory, tree=tree, user=owner, role=TreeRole.OWNER)

    async with session_factory() as session:
        for req in (TreeRole.OWNER, TreeRole.EDITOR, TreeRole.VIEWER):
            assert (
                await check_tree_permission(
                    session, user_id=owner.id, tree_id=tree.id, required=req
                )
                is True
            )


@pytest.mark.asyncio
async def test_owner_via_tree_owner_user_id_fallback(session_factory) -> None:
    """Если нет membership-row, но user.id == tree.owner_user_id — OWNER."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)

    async with session_factory() as session:
        role = await get_user_role_in_tree(session, user_id=owner.id, tree_id=tree.id)
        assert role == TreeRole.OWNER.value

        for req in (TreeRole.OWNER, TreeRole.EDITOR, TreeRole.VIEWER):
            assert (
                await check_tree_permission(
                    session, user_id=owner.id, tree_id=tree.id, required=req
                )
                is True
            )


@pytest.mark.asyncio
async def test_editor_satisfies_editor_and_viewer_only(session_factory) -> None:
    other = await _make_user(session_factory)
    editor = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=other)
    await _add_membership(session_factory, tree=tree, user=editor, role=TreeRole.EDITOR)

    async with session_factory() as session:
        assert (
            await check_tree_permission(
                session, user_id=editor.id, tree_id=tree.id, required=TreeRole.OWNER
            )
            is False
        )
        assert (
            await check_tree_permission(
                session, user_id=editor.id, tree_id=tree.id, required=TreeRole.EDITOR
            )
            is True
        )
        assert (
            await check_tree_permission(
                session, user_id=editor.id, tree_id=tree.id, required=TreeRole.VIEWER
            )
            is True
        )


@pytest.mark.asyncio
async def test_viewer_satisfies_only_viewer(session_factory) -> None:
    other = await _make_user(session_factory)
    viewer = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=other)
    await _add_membership(session_factory, tree=tree, user=viewer, role=TreeRole.VIEWER)

    async with session_factory() as session:
        assert (
            await check_tree_permission(
                session, user_id=viewer.id, tree_id=tree.id, required=TreeRole.OWNER
            )
            is False
        )
        assert (
            await check_tree_permission(
                session, user_id=viewer.id, tree_id=tree.id, required=TreeRole.EDITOR
            )
            is False
        )
        assert (
            await check_tree_permission(
                session, user_id=viewer.id, tree_id=tree.id, required=TreeRole.VIEWER
            )
            is True
        )


@pytest.mark.asyncio
async def test_no_membership_no_access(session_factory) -> None:
    """User без membership и не владелец → False для любой required."""
    other = await _make_user(session_factory)
    stranger = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=other)

    async with session_factory() as session:
        for req in (TreeRole.OWNER, TreeRole.EDITOR, TreeRole.VIEWER):
            assert (
                await check_tree_permission(
                    session, user_id=stranger.id, tree_id=tree.id, required=req
                )
                is False
            )


@pytest.mark.asyncio
async def test_revoked_membership_no_access(session_factory) -> None:
    """``revoked_at`` IS NOT NULL → как-будто membership'а нет."""
    import datetime as dt

    other = await _make_user(session_factory)
    user = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=other)
    m = await _add_membership(session_factory, tree=tree, user=user, role=TreeRole.EDITOR)

    async with session_factory() as session:
        # Revoke
        membership = await session.get(TreeMembership, m.id)
        assert membership is not None
        membership.revoked_at = dt.datetime.now(dt.UTC)
        await session.commit()

    async with session_factory() as session:
        assert (
            await check_tree_permission(
                session, user_id=user.id, tree_id=tree.id, required=TreeRole.VIEWER
            )
            is False
        )


@pytest.mark.asyncio
async def test_unknown_tree_id_no_access(session_factory) -> None:
    user = await _make_user(session_factory)
    fake_tree_id = uuid.uuid4()

    async with session_factory() as session:
        assert (
            await check_tree_permission(
                session, user_id=user.id, tree_id=fake_tree_id, required=TreeRole.VIEWER
            )
            is False
        )
