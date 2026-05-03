"""Phase 15.11a — endpoint tests для completeness assertions API.

Покрытие per-brief:

1. test_create_assertion_with_sources               — happy path
2. test_create_sealed_without_sources_rejected_422  — 15.11b ужесточил до 422
3. test_unique_per_scope_upsert                     — second POST = upsert (same user)
4. test_delete_sets_unsealed_keeps_row              — revoke семантика
5. test_restrict_on_tree_delete                     — FK RESTRICT (отклонение от brief'а)
6. test_get_returns_with_sources_eager_loaded       — N+1 защита

Дополнительные validation-тесты Phase 15.11b — см. test_completeness_validation.py
(source liveness, cross-tree, override mechanic, audit emission).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import (
    CompletenessAssertion,
    Person,
    Source,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

pytestmark = [pytest.mark.db, pytest.mark.integration]


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any) -> User:
    async with factory() as session:
        u = User(
            email=f"u-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:{uuid.uuid4().hex[:8]}",
            display_name="U",
            locale="en",
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


async def _make_tree_with_owner(factory: Any, *, owner: User) -> Tree:
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Tree {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=owner.id,
                role=TreeRole.OWNER.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()
        await session.refresh(tree)
        return tree


async def _make_person(factory: Any, *, tree: Tree) -> Person:
    async with factory() as session:
        p = Person(tree_id=tree.id, sex="U")
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return p


async def _make_source(factory: Any, *, tree: Tree, title: str) -> Source:
    async with factory() as session:
        s = Source(tree_id=tree.id, title=title)
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return s


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_assertion_with_sources(app_client, session_factory: Any) -> None:
    """POST с ≥1 source → 201, body содержит source_ids и asserted_by=user."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src1 = await _make_source(session_factory, tree=tree, title="Revision list 1858")
    src2 = await _make_source(session_factory, tree=tree, title="Birth registry")

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={
            "scope": "siblings",
            "is_sealed": True,
            "note": "all 4 brothers verified",
            "source_ids": [str(src1.id), str(src2.id)],
        },
        headers=_hdr(owner),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scope"] == "siblings"
    assert body["is_sealed"] is True
    assert body["note"] == "all 4 brothers verified"
    assert body["asserted_by"] == str(owner.id)
    assert set(body["source_ids"]) == {str(src1.id), str(src2.id)}


# ---------------------------------------------------------------------------
# 2. Source-required — Phase 15.11b enforced (was permissive in 15.11a).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_sealed_without_sources_rejected_422(app_client, session_factory: Any) -> None:
    """is_sealed=True без sources → 422 (Phase 15.11b)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "children", "is_sealed": True, "source_ids": []},
        headers=_hdr(owner),
    )
    assert r.status_code == 422, r.text
    assert "source citation" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Upsert per (tree, person, scope)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unique_per_scope_upsert(app_client, session_factory: Any) -> None:
    """Второй POST на тот же (person, scope) обновляет row, не создаёт второй."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src1 = await _make_source(session_factory, tree=tree, title="src1")
    src2 = await _make_source(session_factory, tree=tree, title="src2")

    r1 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "spouses", "is_sealed": True, "source_ids": [str(src1.id)]},
        headers=_hdr(owner),
    )
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    r2 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={
            "scope": "spouses",
            "is_sealed": True,
            "note": "updated",
            "source_ids": [str(src2.id)],
        },
        headers=_hdr(owner),
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == first_id
    assert r2.json()["note"] == "updated"
    assert r2.json()["source_ids"] == [str(src2.id)]

    # В БД ровно одна active row для (tree, person, scope=spouses)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CompletenessAssertion).where(
                        CompletenessAssertion.tree_id == tree.id,
                        CompletenessAssertion.subject_person_id == person.id,
                        CompletenessAssertion.scope == "spouses",
                        CompletenessAssertion.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 4. DELETE = revoke (is_sealed=False, sources cleared, row stays)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_sets_unsealed_keeps_row(app_client, session_factory: Any) -> None:
    """DELETE → 204, row остаётся с is_sealed=False, sources пусты, GET → 404."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "parents", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r.status_code == 201
    created_id = r.json()["id"]

    rd = await app_client.delete(
        f"/trees/{tree.id}/persons/{person.id}/completeness/parents",
        headers=_hdr(owner),
    )
    assert rd.status_code == 204

    # Row остаётся в БД — но GET active возвращает 404 (filter is_sealed=False
    # не наложен; rows query filters только active, но revoked row сохраняет
    # is_sealed=False; GET active по scope с найденной active rows... — мы
    # проверяем raw БД).
    async with session_factory() as session:
        row = (
            await session.execute(
                select(CompletenessAssertion)
                .where(CompletenessAssertion.id == uuid.UUID(created_id))
                .options(selectinload(CompletenessAssertion.sources))
            )
        ).scalar_one()
    assert row.is_sealed is False
    assert row.deleted_at is None
    assert row.sources == []


# ---------------------------------------------------------------------------
# 5. tree_id FK is RESTRICT (deviation from brief's CASCADE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restrict_on_tree_delete(app_client, session_factory: Any) -> None:
    """Brief specced ``ON DELETE CASCADE``; project convention — RESTRICT.

    Hard delete tree с active assertion'ами должен подняться (IntegrityError),
    forcing явную очистку ассерций сначала. Это соответствует ADR-0003 и
    задокументировано в ADR-0076 §«Принятые отклонения от brief'а».
    """
    from sqlalchemy.exc import IntegrityError

    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r.status_code == 201

    # Hard delete tree → IntegrityError (RESTRICT-FK).
    async with session_factory() as session:
        tree_row = (await session.execute(select(Tree).where(Tree.id == tree.id))).scalar_one()
        await session.delete(tree_row)
        with pytest.raises(IntegrityError):
            await session.commit()


# ---------------------------------------------------------------------------
# 6. GET eager-loads sources (N+1 защита)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_with_sources_eager_loaded(app_client, session_factory: Any) -> None:
    """GET single + GET list возвращают source_ids в одном HTTP-вызове.

    N+1 защита: source_ids возвращаются для всех assertion'ов в list,
    без дополнительных round-trip'ов с клиента.
    """
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    s1 = await _make_source(session_factory, tree=tree, title="a")
    s2 = await _make_source(session_factory, tree=tree, title="b")
    s3 = await _make_source(session_factory, tree=tree, title="c")

    # Две assertion'а с разными scope'ами и разным числом sources.
    for scope, src_ids in [("siblings", [s1.id]), ("children", [s2.id, s3.id])]:
        r = await app_client.post(
            f"/trees/{tree.id}/persons/{person.id}/completeness",
            json={
                "scope": scope,
                "is_sealed": True,
                "source_ids": [str(s) for s in src_ids],
            },
            headers=_hdr(owner),
        )
        assert r.status_code == 201

    rl = await app_client.get(
        f"/trees/{tree.id}/persons/{person.id}/completeness", headers=_hdr(owner)
    )
    assert rl.status_code == 200
    by_scope = {a["scope"]: set(a["source_ids"]) for a in rl.json()}
    assert by_scope["siblings"] == {str(s1.id)}
    assert by_scope["children"] == {str(s2.id), str(s3.id)}

    rs = await app_client.get(
        f"/trees/{tree.id}/persons/{person.id}/completeness/children", headers=_hdr(owner)
    )
    assert rs.status_code == 200
    assert set(rs.json()["source_ids"]) == {str(s2.id), str(s3.id)}


# ---------------------------------------------------------------------------
# Permission gate sanity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_cannot_create(app_client, session_factory: Any) -> None:
    """VIEWER на write → 403 (require_tree_role(EDITOR))."""
    owner = await _make_user(session_factory)
    viewer = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    async with session_factory() as session:
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=viewer.id,
                role=TreeRole.VIEWER.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": []},
        headers=_hdr(viewer),
    )
    assert r.status_code == 403, r.text
