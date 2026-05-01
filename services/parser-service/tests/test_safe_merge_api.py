"""Phase 5.7b — Safe Merge endpoint integration tests.

Покрывают:

* POST /api/v1/trees/{id}/merge — happy path: 1 person_added, persists в БД.
* missing_anchor → 200 + aborted=true + persons НЕ в БД (atomic rollback).
* field_overlap + policy=prefer_left → applied=[], skipped с конфликтом.
* EDITOR-permission gate (OWNER ok, viewer 403).

Эти тесты дополняют чистые юнит-тесты в
``packages/gedcom-parser/tests/merge/test_apply_basic.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import (
    Person,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"safemerge-{uuid.uuid4().hex[:8]}@example.com"
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


async def _make_tree_with_owner(factory: Any, *, owner: User) -> Tree:
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Safe Merge Test {uuid.uuid4().hex[:6]}",
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


async def _add_membership(factory: Any, *, tree: Tree, user: User, role: TreeRole) -> None:
    async with factory() as session:
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=user.id,
                role=role.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()


async def _add_person_with_xref(
    factory: Any,
    *,
    tree: Tree,
    xref: str,
    sex: str = "U",
) -> Person:
    async with factory() as session:
        p = Person(
            tree_id=tree.id,
            gedcom_xref=xref,
            sex=sex,
            provenance={},
            version_id=1,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return p


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_merge_persons_added_persists(app_client, session_factory: Any) -> None:
    """1 person_added → 200, applied=[person_added], в БД появилась персона."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    body = {
        "diff_report": {
            "persons_added": [{"xref": "@INEW@", "fields": {"sex": "M"}}],
        },
        "policy": {"on_conflict": "manual"},
    }

    r = await app_client.post(
        f"/api/v1/trees/{tree.id}/merge",
        json=body,
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["aborted"] is False
    assert any(c["kind"] == "person_added" for c in payload["applied"])

    # Sanity: персона действительно записана в БД.
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Person).where(
                        Person.tree_id == tree.id,
                        Person.gedcom_xref == "@INEW@",
                        Person.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].sex == "M"


@pytest.mark.asyncio
async def test_safe_merge_missing_anchor_rolls_back(app_client, session_factory: Any) -> None:
    """missing_anchor → 200 + aborted=true; ни одна персона из persons_added
    не должна попасть в БД (atomic abort)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    body = {
        "diff_report": {
            "persons_added": [{"xref": "@IGOOD@", "fields": {"sex": "F"}}],
            "relations_added": [
                {
                    "relation_type": "parent_child",
                    "person_a_xref": "@I_GHOST@",
                    "person_b_xref": "@IGOOD@",
                }
            ],
        },
        "policy": {"on_conflict": "prefer_right"},
    }

    r = await app_client.post(
        f"/api/v1/trees/{tree.id}/merge",
        json=body,
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["aborted"] is True
    assert payload["applied"] == []
    assert any(c["kind"] == "missing_anchor" for c in payload["skipped"])

    # @IGOOD@ НЕ должен оказаться в БД (хотя без relation_added он бы applied).
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(Person).where(
                        Person.tree_id == tree.id,
                        Person.gedcom_xref == "@IGOOD@",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == []


@pytest.mark.asyncio
async def test_safe_merge_field_overlap_prefer_left(app_client, session_factory: Any) -> None:
    """Существующий xref + конфликт sex + policy=prefer_left → target не меняется."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_person_with_xref(session_factory, tree=tree, xref="@I1@", sex="M")

    body = {
        "diff_report": {
            "persons_modified": [
                {
                    "target_xref": "@I1@",
                    "field_changes": {"sex": {"before": "F", "after": "F"}},
                }
            ]
        },
        "policy": {"on_conflict": "prefer_left"},
    }

    r = await app_client.post(
        f"/api/v1/trees/{tree.id}/merge",
        json=body,
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["aborted"] is False
    assert payload["applied"] == []  # ничего не записывали — owner's value won
    actions = [a["action"] for a in payload["log"]]
    assert "applied_prefer_left" in actions

    # БД не изменилась.
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Person).where(Person.tree_id == tree.id, Person.gedcom_xref == "@I1@")
            )
        ).scalar_one()
    assert row.sex == "M"


@pytest.mark.asyncio
async def test_safe_merge_field_overlap_manual_records_conflict(
    app_client, session_factory: Any
) -> None:
    """Конфликт + policy=manual → skipped содержит field_overlap, БД без изменений."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_person_with_xref(session_factory, tree=tree, xref="@I1@", sex="M")

    body = {
        "diff_report": {
            "persons_modified": [
                {
                    "target_xref": "@I1@",
                    "field_changes": {"sex": {"before": "F", "after": "F"}},
                }
            ]
        },
        "policy": {"on_conflict": "manual"},
    }

    r = await app_client.post(
        f"/api/v1/trees/{tree.id}/merge",
        json=body,
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["aborted"] is False
    assert payload["applied"] == []
    assert len(payload["skipped"]) == 1
    assert payload["skipped"][0]["kind"] == "field_overlap"
    assert payload["skipped"][0]["target_xref"] == "@I1@"


@pytest.mark.asyncio
async def test_safe_merge_viewer_forbidden(app_client, session_factory: Any) -> None:
    """VIEWER — 403; для merge нужен ≥ EDITOR."""
    owner = await _make_user(session_factory)
    viewer = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_membership(session_factory, tree=tree, user=viewer, role=TreeRole.VIEWER)

    body = {"diff_report": {}, "policy": {"on_conflict": "manual"}}

    r = await app_client.post(
        f"/api/v1/trees/{tree.id}/merge",
        json=body,
        headers=_hdr(viewer),
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_safe_merge_unknown_tree_404(app_client, session_factory: Any) -> None:
    """Несуществующий tree_id → 404."""
    owner = await _make_user(session_factory)

    body = {"diff_report": {}, "policy": {"on_conflict": "manual"}}

    r = await app_client.post(
        f"/api/v1/trees/{uuid.uuid4()}/merge",
        json=body,
        headers=_hdr(owner),
    )
    assert r.status_code == 404, r.text
