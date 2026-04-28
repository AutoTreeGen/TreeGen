"""Phase 11.1 sharing tests: audit-log, transfer-owner, resend.

Дополнительные интеграционные тесты к существующему ``test_sharing.py``
(Phase 11.0). Не дублируют покрытие 11.0 — только новые endpoints.

Маркеры: ``db`` + ``integration``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from parser_service.api import sharing as sharing_module
from parser_service.services import email_dispatcher
from shared_models import TreeRole
from shared_models.orm import AuditLog, Tree, TreeInvitation, TreeMembership, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers — копия из test_sharing.py чтобы тесты были self-contained.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"p11_1-{uuid.uuid4().hex[:8]}@example.com"
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


async def _make_tree_with_owner_membership(factory: Any, *, owner: User) -> Tree:
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"P11.1 Test {uuid.uuid4().hex[:6]}",
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
    return {"X-User-Id": str(user.id)}


@pytest.fixture(autouse=True)
def _reset_resend_state() -> None:
    """Очищаем in-memory rate-limit map между тестами."""
    sharing_module._RESEND_LAST_AT.clear()


# ---------------------------------------------------------------------------
# Email dispatcher stub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_dispatcher_stub_logs_only(caplog) -> None:
    """``send_share_invite`` пишет лог, не падает, не делает сети."""
    import logging

    with caplog.at_level(logging.INFO, logger=email_dispatcher.__name__):
        await email_dispatcher.send_share_invite(
            invitation_token="abc-123",
            recipient_email="invitee@example.com",
            tree_name="Test Tree",
            inviter_name="Owner Name",
        )

    matches = [r for r in caplog.records if r.message and "share_invite stub" in r.message]
    assert matches, "expected one log line from stub"
    record = matches[0]
    assert getattr(record, "idempotency_key", None) == "invite:abc-123"
    assert getattr(record, "kind", None) == "share_invite"


# ---------------------------------------------------------------------------
# GET /trees/{id}/audit-log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_owner_only(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    intruder = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.get(f"/trees/{tree.id}/audit-log", headers=_hdr(intruder))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_audit_log_filter_validation(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    # Unknown entity_type → 400.
    r = await app_client.get(
        f"/trees/{tree.id}/audit-log?entity_type=secret_table",
        headers=_hdr(owner),
    )
    assert r.status_code == 400

    # Allowed entity_type (даже если 0 строк) → 200.
    r = await app_client.get(
        f"/trees/{tree.id}/audit-log?entity_type=tree_memberships",
        headers=_hdr(owner),
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_audit_log_pagination(app_client, session_factory: Any) -> None:
    """Вставляем 5 audit-rows под уникальным entity_type и проверяем limit/offset.

    Audit-log auto-listener (Phase 2) пишет свои строки на каждый INSERT
    через сессии теста — их игнорируем фильтром по entity_type, чтобы тест
    не зависел от их количества.
    """
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    async with session_factory() as session:
        for i in range(5):
            session.add(
                AuditLog(
                    tree_id=tree.id,
                    entity_type="tree_invitations",  # фильтруемый ниже
                    entity_id=uuid.uuid4(),
                    action="insert",
                    actor_user_id=owner.id,
                    actor_kind="user",
                    diff={"i": i},
                )
            )
        await session.commit()

    r = await app_client.get(
        f"/trees/{tree.id}/audit-log?entity_type=tree_invitations&limit=2&offset=0",
        headers=_hdr(owner),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    r2 = await app_client.get(
        f"/trees/{tree.id}/audit-log?entity_type=tree_invitations&limit=2&offset=4",
        headers=_hdr(owner),
    )
    assert r2.json()["total"] == 5
    assert len(r2.json()["items"]) == 1


# ---------------------------------------------------------------------------
# PATCH /trees/{id}/transfer-owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_owner_happy_path(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    new_owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    # new_owner становится EDITOR via invite+accept.
    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": new_owner.email, "role": "editor"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]
    await app_client.post(f"/invitations/{token}/accept", headers=_hdr(new_owner))

    r = await app_client.patch(
        f"/trees/{tree.id}/transfer-owner",
        json={
            "new_owner_email": new_owner.email,
            "current_owner_email_confirmation": owner.email,
        },
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["previous_owner_user_id"] == str(owner.id)
    assert body["new_owner_user_id"] == str(new_owner.id)

    # После transfer: new_owner может invite'ить, owner — не может.
    r_invite = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": "another@example.com", "role": "viewer"},
        headers=_hdr(new_owner),
    )
    assert r_invite.status_code == 201

    r_old = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": "another2@example.com", "role": "viewer"},
        headers=_hdr(owner),
    )
    assert r_old.status_code == 403


@pytest.mark.asyncio
async def test_transfer_owner_email_mismatch_rejected(app_client, session_factory: Any) -> None:
    """``current_owner_email_confirmation`` ≠ caller email → 400."""
    owner = await _make_user(session_factory)
    new_owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": new_owner.email, "role": "editor"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]
    await app_client.post(f"/invitations/{token}/accept", headers=_hdr(new_owner))

    r = await app_client.patch(
        f"/trees/{tree.id}/transfer-owner",
        json={
            "new_owner_email": new_owner.email,
            "current_owner_email_confirmation": "wrong@example.com",
        },
        headers=_hdr(owner),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_transfer_owner_to_self_rejected(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.patch(
        f"/trees/{tree.id}/transfer-owner",
        json={
            "new_owner_email": owner.email,
            "current_owner_email_confirmation": owner.email,
        },
        headers=_hdr(owner),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_transfer_owner_to_non_member_rejected(app_client, session_factory: Any) -> None:
    """new_owner_email не в active members → 404."""
    owner = await _make_user(session_factory)
    stranger = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    r = await app_client.patch(
        f"/trees/{tree.id}/transfer-owner",
        json={
            "new_owner_email": stranger.email,
            "current_owner_email_confirmation": owner.email,
        },
        headers=_hdr(owner),
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /trees/invitations/{token}/resend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resend_owner_only(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    intruder = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": "guest@example.com", "role": "viewer"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]

    r = await app_client.post(
        f"/trees/invitations/{token}/resend",
        headers=_hdr(intruder),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_resend_happy_path_then_rate_limited(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": "guest@example.com", "role": "viewer"},
        headers=_hdr(owner),
    )
    token = create.json()["token"]

    r1 = await app_client.post(f"/trees/invitations/{token}/resend", headers=_hdr(owner))
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["invitee_email"] == "guest@example.com"

    # Сразу второй resend — 429.
    r2 = await app_client.post(f"/trees/invitations/{token}/resend", headers=_hdr(owner))
    assert r2.status_code == 429


@pytest.mark.asyncio
async def test_resend_rejects_revoked_invitation(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    create = await app_client.post(
        f"/trees/{tree.id}/invitations",
        json={"email": "guest@example.com", "role": "viewer"},
        headers=_hdr(owner),
    )
    invitation_id = create.json()["id"]
    token = create.json()["token"]

    await app_client.delete(f"/invitations/{invitation_id}", headers=_hdr(owner))

    r = await app_client.post(f"/trees/invitations/{token}/resend", headers=_hdr(owner))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_resend_rejects_expired_invitation(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner_membership(session_factory, owner=owner)

    async with session_factory() as session:
        inv = TreeInvitation(
            tree_id=tree.id,
            inviter_user_id=owner.id,
            invitee_email="expired@example.com",
            role="viewer",
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        token = inv.token

    r = await app_client.post(f"/trees/invitations/{token}/resend", headers=_hdr(owner))
    assert r.status_code == 409
