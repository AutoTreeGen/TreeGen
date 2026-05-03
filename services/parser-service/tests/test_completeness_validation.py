"""Phase 15.11b — endpoint tests для validation layer над 15.11a.

Покрытие per-brief (12 тест-кейсов; «researcher»-тест из brief'а пропущен,
т.к. в кодовой базе нет ``RESEARCHER`` роли — см. ADR-0077 §«Принятые
отклонения от brief'а»):

1. test_create_sealed_without_source_rejects_422
2. test_create_with_source_from_different_tree_rejects_422
3. test_create_with_deleted_source_rejects_422
4. test_create_with_unknown_source_rejects_422
5. test_viewer_role_rejected_403                       — framework gate
6. test_owner_can_assert
7. test_editor_can_assert
8. test_reassert_by_different_user_without_override_rejects_409
9. test_reassert_by_different_user_with_override_succeeds_and_audits
10. test_reassert_by_same_user_idempotent_no_override_audit
11. test_revoke_by_viewer_rejected_403
12. test_revoke_by_owner_succeeds_and_audits

Все тесты — integration-уровня (DB hit), стиль 15.11a's test_completeness_api.py.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.enums import ActorKind
from shared_models.orm import (
    AuditLog,
    Person,
    Source,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


async def _make_tree_with_role(factory: Any, *, owner: User, member: User, role: TreeRole) -> Tree:
    """Tree owned by ``owner``, with ``member`` enrolled at ``role``."""
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
        if member.id != owner.id:
            session.add(
                TreeMembership(
                    tree_id=tree.id,
                    user_id=member.id,
                    role=role.value,
                    accepted_at=dt.datetime.now(dt.UTC),
                )
            )
        await session.commit()
        await session.refresh(tree)
        return tree


async def _make_tree_with_owner(factory: Any, *, owner: User) -> Tree:
    return await _make_tree_with_role(factory, owner=owner, member=owner, role=TreeRole.OWNER)


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


async def _soft_delete_source(factory: Any, *, source: Source) -> None:
    async with factory() as session:
        row = (await session.execute(select(Source).where(Source.id == source.id))).scalar_one()
        row.deleted_at = dt.datetime.now(dt.UTC)
        await session.commit()


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


async def _override_audit_rows(
    factory: Any, *, assertion_id: uuid.UUID, reason: str
) -> list[AuditLog]:
    """Достать manual audit-rows c указанным reason для assertion'а."""
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "completeness_assertions",
                AuditLog.entity_id == assertion_id,
                AuditLog.reason == reason,
            )
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 1. Source-required (sealed without source → 422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_sealed_without_source_rejects_422(app_client, session_factory: Any) -> None:
    """is_sealed=True + source_ids=[] → 422."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": []},
        headers=_hdr(owner),
    )
    assert r.status_code == 422, r.text
    assert "source citation" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 2. Source must belong to same tree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_source_from_different_tree_rejects_422(
    app_client, session_factory: Any
) -> None:
    """source_id указывает на source из другого дерева → 422.

    Privacy-граница: caller имеет доступ к ``other_tree``, но не может
    «протащить» оттуда source в ассерцию ``tree``.
    """
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    other_tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    foreign_src = await _make_source(session_factory, tree=other_tree, title="Foreign")

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={
            "scope": "children",
            "is_sealed": True,
            "source_ids": [str(foreign_src.id)],
        },
        headers=_hdr(owner),
    )
    assert r.status_code == 422, r.text
    assert "tree" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Source must not be soft-deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_deleted_source_rejects_422(app_client, session_factory: Any) -> None:
    """source.deleted_at IS NOT NULL → 422."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="Deleted")
    await _soft_delete_source(session_factory, source=src)

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "spouses", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r.status_code == 422, r.text
    assert "deleted" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Unknown source id → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_unknown_source_rejects_422(app_client, session_factory: Any) -> None:
    """source_id не существует в БД → 422."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={
            "scope": "parents",
            "is_sealed": True,
            "source_ids": [str(uuid.uuid4())],
        },
        headers=_hdr(owner),
    )
    assert r.status_code == 422, r.text
    assert "not found" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. Role gate — viewer rejected on POST (framework-level via require_tree_role)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_role_rejected_403(app_client, session_factory: Any) -> None:
    """Viewer пытается create assertion → 403 (framework-level role gate)."""
    owner = await _make_user(session_factory)
    viewer = await _make_user(session_factory)
    tree = await _make_tree_with_role(
        session_factory, owner=owner, member=viewer, role=TreeRole.VIEWER
    )
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(viewer),
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# 6. Owner can assert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_can_assert(app_client, session_factory: Any) -> None:
    """OWNER role → 201."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "children", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 7. Editor can assert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_editor_can_assert(app_client, session_factory: Any) -> None:
    """EDITOR role → 201."""
    owner = await _make_user(session_factory)
    editor = await _make_user(session_factory)
    tree = await _make_tree_with_role(
        session_factory, owner=owner, member=editor, role=TreeRole.EDITOR
    )
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(editor),
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 8. Re-assert by different user without override → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassert_by_different_user_without_override_rejects_409(
    app_client, session_factory: Any
) -> None:
    """User B пытается перезаписать assertion'у user A без override → 409."""
    owner = await _make_user(session_factory)
    editor = await _make_user(session_factory)
    tree = await _make_tree_with_role(
        session_factory, owner=owner, member=editor, role=TreeRole.EDITOR
    )
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    # Owner создаёт первую ассерцию.
    r1 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r1.status_code == 201, r1.text

    # Editor (другой user) пытается её перезаписать без override → 409.
    r2 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(editor),
    )
    assert r2.status_code == 409, r2.text
    assert "override" in r2.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 9. Re-assert by different user with override → 201 + audit row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassert_by_different_user_with_override_succeeds_and_audits(
    app_client, session_factory: Any
) -> None:
    """override=True от другого user'а → 201 + audit-row с reason=override_reassertion."""
    owner = await _make_user(session_factory)
    editor = await _make_user(session_factory)
    tree = await _make_tree_with_role(
        session_factory, owner=owner, member=editor, role=TreeRole.EDITOR
    )
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r1 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "spouses", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r1.status_code == 201
    assertion_id = uuid.UUID(r1.json()["id"])

    r2 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={
            "scope": "spouses",
            "is_sealed": True,
            "source_ids": [str(src.id)],
            "override": True,
        },
        headers=_hdr(editor),
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["asserted_by"] == str(editor.id)

    # Audit row должна быть ровно одна с reason=override_reassertion.
    rows = await _override_audit_rows(
        session_factory, assertion_id=assertion_id, reason="override_reassertion"
    )
    assert len(rows) == 1
    audit = rows[0]
    assert audit.actor_user_id == editor.id
    assert audit.actor_kind == ActorKind.USER.value
    assert audit.diff["prev_actor_id"] == str(owner.id)
    assert audit.diff["new_actor_id"] == str(editor.id)
    assert audit.diff["scope"] == "spouses"


# ---------------------------------------------------------------------------
# 10. Re-assert by same user — idempotent, no override audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassert_by_same_user_idempotent_no_override_audit(
    app_client, session_factory: Any
) -> None:
    """Same-user re-assert → 201 (upsert), audit-row override НЕ пишется."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r1 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "parents", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r1.status_code == 201
    assertion_id = uuid.UUID(r1.json()["id"])

    # Same user — note меняется, но override-audit не пишется.
    r2 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={
            "scope": "parents",
            "is_sealed": True,
            "note": "second pass",
            "source_ids": [str(src.id)],
        },
        headers=_hdr(owner),
    )
    assert r2.status_code == 201
    assert r2.json()["note"] == "second pass"

    rows = await _override_audit_rows(
        session_factory, assertion_id=assertion_id, reason="override_reassertion"
    )
    assert rows == []


# ---------------------------------------------------------------------------
# 11. Revoke by viewer — 403 (framework-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_by_viewer_rejected_403(app_client, session_factory: Any) -> None:
    """Viewer DELETE → 403."""
    owner = await _make_user(session_factory)
    viewer = await _make_user(session_factory)
    tree = await _make_tree_with_role(
        session_factory, owner=owner, member=viewer, role=TreeRole.VIEWER
    )
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r1 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "siblings", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r1.status_code == 201

    rd = await app_client.delete(
        f"/trees/{tree.id}/persons/{person.id}/completeness/siblings",
        headers=_hdr(viewer),
    )
    assert rd.status_code == 403, rd.text


# ---------------------------------------------------------------------------
# 12. Revoke by owner — 204 + audit row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_by_owner_succeeds_and_audits(app_client, session_factory: Any) -> None:
    """Owner DELETE → 204, audit-row reason=revoke с prev_actor metadata."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    person = await _make_person(session_factory, tree=tree)
    src = await _make_source(session_factory, tree=tree, title="src")

    r1 = await app_client.post(
        f"/trees/{tree.id}/persons/{person.id}/completeness",
        json={"scope": "children", "is_sealed": True, "source_ids": [str(src.id)]},
        headers=_hdr(owner),
    )
    assert r1.status_code == 201
    assertion_id = uuid.UUID(r1.json()["id"])

    rd = await app_client.delete(
        f"/trees/{tree.id}/persons/{person.id}/completeness/children",
        headers=_hdr(owner),
    )
    assert rd.status_code == 204, rd.text

    rows = await _override_audit_rows(session_factory, assertion_id=assertion_id, reason="revoke")
    assert len(rows) == 1
    audit = rows[0]
    assert audit.actor_user_id == owner.id
    assert audit.actor_kind == ActorKind.USER.value
    assert audit.diff["scope"] == "children"
    assert audit.diff["prev_actor_id"] == str(owner.id)
    assert audit.diff["revoking_actor_id"] == str(owner.id)
