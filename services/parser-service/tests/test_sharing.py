"""Интеграционные тесты sharing API (Phase 11.0).

Покрывают:

* ``POST /trees/{id}/invitations`` — owner-only (403 для не-owner).
* ``POST /invitations/{token}/accept`` — happy path, expired (410), revoked (410),
  already-accepted-by-other (409), already-accepted-by-self (idempotent).
* ``GET /trees/{id}/members`` — list active memberships.
* ``PATCH /memberships/{id}`` — owner меняет EDITOR↔VIEWER, не может демоутить
  OWNER, не может менять свою роль.
* ``DELETE /memberships/{id}`` — soft-revoke, нельзя revoke'ить OWNER-row.
* Permission gates на существующих endpoints (``GET /trees/{id}/persons``)
  пускают viewer'а через X-User-Id header.

Маркеры: ``db`` + ``integration`` — testcontainers Postgres.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import Tree, TreeInvitation, TreeMembership, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers — создание ресурсов через прямую сессию (минуя FastAPI app).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"share-{uuid.uuid4().hex[:8]}@example.com"
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


async def _make_tree_with_owner_membership(
    factory: Any,
    *,
    owner: User,
) -> Tree:
    """Создать tree + явный OWNER membership-row (как сделает Phase 11.1 create-flow)."""
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Share Test {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        m = TreeMembership(
            tree_id=tree.id,
            user_id=owner.id,
            role=TreeRole.OWNER.value,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(m)
        await session.commit()
        await session.refresh(tree)
        return tree


def _hdr(user: User) -> dict[str, str]:
    """X-User-Id header для auth-stub'а."""
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# POST /trees/{tree_id}/invitations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_invitation_owner_only(app_client, session_factory: Any) -> None:
    """Не-owner получает 403."""
    owner = await _make_user(session_factory)
    intruder = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": "guest@example.com", "role": "viewer"},
        headers=_hdr(intruder),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_invitation_happy_path(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": "GUEST@Example.COM", "role": "editor"},
        headers=_hdr(owner),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["invitee_email"] == "guest@example.com"  # normalized
    assert body["role"] == "editor"
    assert "token" in body
    assert body["invite_url"].endswith(body["token"])


@pytest.mark.asyncio
async def test_list_invitations_owner_only(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    intruder = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.get(f"/trees/{tree.id}/invitations", headers=_hdr(intruder))
    assert r.status_code == 403, f"intruder unexpected {r.status_code}: {r.text}"

    r = await app_client.get(f"/trees/{tree.id}/invitations", headers=_hdr(owner))
    assert r.status_code == 200, f"owner unexpected {r.status_code}: {r.text}"
    assert r.json()["items"] == []


# ---------------------------------------------------------------------------
# POST /invitations/{token}/accept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_invitation_creates_membership(app_client, session_factory: Any) -> None:
    """Happy path: accept создаёт active membership и помечает invitation."""
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    # Owner создаёт invitation.
    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": invitee.email, "role": "editor"},
        headers=_hdr(owner),
    )
    assert create.status_code == 201
    token = create.json()["token"]

    # Invitee accept.
    r = await app_client.post(
        f"/invitations/{token}/accept",
        headers=_hdr(invitee),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["tree_id"] == str(tree.id)
    assert body["role"] == "editor"

    # Membership active в БД.
    async with session_factory() as session:
        from sqlalchemy import select

        m = await session.scalar(
            select(TreeMembership).where(
                TreeMembership.tree_id == tree.id,
                TreeMembership.user_id == invitee.id,
            )
        )
        assert m is not None
        assert m.role == "editor"
        assert m.revoked_at is None
        assert m.accepted_at is not None
        assert m.invited_by == owner.id


@pytest.mark.asyncio
async def test_accept_invitation_expired_token(app_client, session_factory: Any) -> None:
    """410 Gone, если ``expires_at`` в прошлом."""
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    # Создаём invitation напрямую с expired-датой.
    async with session_factory() as session:
        inv = TreeInvitation(
            tree_id=tree.id,
            inviter_user_id=owner.id,
            invitee_email=invitee.email,
            role="viewer",
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        token = inv.token

    r = await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))
    assert r.status_code == 410
    assert "expired" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_accept_invitation_revoked_token(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": invitee.email, "role": "viewer"},
        headers=_hdr(owner),
    )
    invitation_id = create.json()["id"]
    token = create.json()["token"]

    # Owner revoke'ит.
    r_del = await app_client.delete(f"/invitations/{invitation_id}", headers=_hdr(owner))
    assert r_del.status_code == 204

    # Invitee пытается accept — 410.
    r = await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))
    assert r.status_code == 410
    assert "revoked" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_accept_invitation_idempotent_for_same_user(app_client, session_factory: Any) -> None:
    """Повторный accept тем же user'ом возвращает existing membership."""
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": invitee.email, "role": "viewer"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]

    r1 = await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))
    r2 = await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["membership_id"] == r2.json()["membership_id"]


@pytest.mark.asyncio
async def test_accept_invitation_conflict_for_other_user(app_client, session_factory: Any) -> None:
    """Если инвайт уже accepted одним user'ом — другой получает 409."""
    owner = await _make_user(session_factory)
    first = await _make_user(session_factory)
    second = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": first.email, "role": "viewer"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]

    await app_client.post(f"/invitations/{token}/accept", headers=_hdr(first))
    r = await app_client.post(f"/invitations/{token}/accept", headers=_hdr(second))
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# GET /trees/{id}/members + PATCH/DELETE memberships
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_members_includes_owner_and_accepted_invitees(
    app_client, session_factory: Any
) -> None:
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": invitee.email, "role": "editor"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]
    await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))

    r = await app_client.get(f"/trees/{tree.id}/members", headers=_hdr(owner))
    assert r.status_code == 200
    items = r.json()["items"]
    roles_by_email = {item["email"]: item["role"] for item in items}
    assert roles_by_email[owner.email] == "owner"
    assert roles_by_email[invitee.email] == "editor"


@pytest.mark.asyncio
async def test_owner_cannot_demote_self(app_client, session_factory: Any) -> None:
    """409 при попытке поменять собственную OWNER-роль (нужен сначала transfer)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    members = await app_client.get(f"/trees/{tree.id}/members", headers=_hdr(owner))
    owner_membership_id = next(
        item["id"] for item in members.json()["items"] if item["email"] == owner.email
    )

    r = await app_client.patch(
        f"/memberships/{owner_membership_id}",
        json={"role": "editor"},
        headers=_hdr(owner),
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_change_member_role_owner_only(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": invitee.email, "role": "viewer"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]
    accept = await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))
    membership_id = accept.json()["membership_id"]

    # Не-owner — 403.
    r_forbid = await app_client.patch(
        f"/memberships/{membership_id}",
        json={"role": "editor"},
        headers=_hdr(invitee),
    )
    assert r_forbid.status_code == 403

    # Owner — 200.
    r_ok = await app_client.patch(
        f"/memberships/{membership_id}",
        json={"role": "editor"},
        headers=_hdr(owner),
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["role"] == "editor"


@pytest.mark.asyncio
async def test_revoke_membership_owner_only(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": invitee.email, "role": "viewer"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]
    accept = await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))
    membership_id = accept.json()["membership_id"]

    # Owner revoke'ит.
    r = await app_client.delete(f"/memberships/{membership_id}", headers=_hdr(owner))
    assert r.status_code == 204

    # После revoke invitee лишается доступа: GET /trees/{id}/persons → 403.
    r_persons = await app_client.get(f"/trees/{tree.id}/persons", headers=_hdr(invitee))
    assert r_persons.status_code == 403


@pytest.mark.asyncio
async def test_cannot_revoke_owner_membership(app_client, session_factory: Any) -> None:
    """409 — нельзя revoke'ить OWNER-row (нужен transfer)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    members = await app_client.get(f"/trees/{tree.id}/members", headers=_hdr(owner))
    owner_membership_id = next(
        item["id"] for item in members.json()["items"] if item["email"] == owner.email
    )

    r = await app_client.delete(f"/memberships/{owner_membership_id}", headers=_hdr(owner))
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Permission gate on existing endpoint — VIEWER может читать persons.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_can_list_persons_after_accept(app_client, session_factory: Any) -> None:
    """VIEWER через accept'ed invitation получает 200 на ``GET /trees/{id}/persons``."""
    owner = await _make_user(session_factory)
    invitee = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": invitee.email, "role": "viewer"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]
    await app_client.post(f"/invitations/{token}/accept", headers=_hdr(invitee))

    r = await app_client.get(f"/trees/{tree.id}/persons", headers=_hdr(invitee))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_stranger_cannot_list_persons(app_client, session_factory: Any) -> None:
    """Без membership и не-owner — 403."""
    owner = await _make_user(session_factory)
    stranger = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.get(f"/trees/{tree.id}/persons", headers=_hdr(stranger))
    assert r.status_code == 403
