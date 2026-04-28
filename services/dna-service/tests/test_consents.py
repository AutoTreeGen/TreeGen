"""Базовые smoke-тесты consent CRUD endpoints (Task 3 scope).

Глубокие flow-тесты (revoke deletes blobs, cross-consent enforcement)
живут в test_consents_flow.py (Task 4). Audit-log invariants —
в test_consents_audit.py.
"""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.db
@pytest.mark.integration
async def test_create_and_get_consent(app_client, seeded_user_and_tree) -> None:
    user_id, tree_id = seeded_user_and_tree

    create_resp = await app_client.post(
        "/consents",
        json={
            "tree_id": str(tree_id),
            "user_id": str(user_id),
            "kit_owner_email": "kit-owner@example.com",
            "consent_text": "I consent to processing my DNA data.",
            "consent_version": "1.0",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    assert created["is_active"] is True
    assert created["revoked_at"] is None
    assert created["kit_owner_email"] == "kit-owner@example.com"

    get_resp = await app_client.get(f"/consents/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == created["id"]


@pytest.mark.db
@pytest.mark.integration
async def test_get_consent_returns_404_for_unknown(app_client) -> None:
    response = await app_client.get("/consents/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_revoke_consent_marks_inactive(app_client, seeded_user_and_tree) -> None:
    user_id, tree_id = seeded_user_and_tree

    create_resp = await app_client.post(
        "/consents",
        json={
            "tree_id": str(tree_id),
            "user_id": str(user_id),
            "kit_owner_email": "owner@example.com",
            "consent_text": "consent",
        },
    )
    consent_id = create_resp.json()["id"]

    delete_resp = await app_client.delete(f"/consents/{consent_id}")
    assert delete_resp.status_code == 204

    get_resp = await app_client.get(f"/consents/{consent_id}")
    payload = get_resp.json()
    assert payload["is_active"] is False
    assert payload["revoked_at"] is not None


@pytest.mark.db
@pytest.mark.integration
async def test_revoke_is_idempotent(app_client, seeded_user_and_tree) -> None:
    user_id, tree_id = seeded_user_and_tree
    create_resp = await app_client.post(
        "/consents",
        json={
            "tree_id": str(tree_id),
            "user_id": str(user_id),
            "kit_owner_email": "owner@example.com",
            "consent_text": "consent",
        },
    )
    consent_id = create_resp.json()["id"]

    first = await app_client.delete(f"/consents/{consent_id}")
    second = await app_client.delete(f"/consents/{consent_id}")
    assert first.status_code == 204
    assert second.status_code == 204


@pytest.mark.db
@pytest.mark.integration
async def test_list_user_consents_returns_active_and_revoked(
    app_client, seeded_user_and_tree
) -> None:
    """GET /users/{user_id}/consents возвращает все consent'ы пользователя.

    Активные и revoked — оба должны попасть в выборку (consent rows
    остаются навсегда per ADR-0012 для GDPR audit-trail).
    """
    user_id, tree_id = seeded_user_and_tree

    payload_a = {
        "tree_id": str(tree_id),
        "user_id": str(user_id),
        "kit_owner_email": "first@example.com",
        "consent_text": "consent",
    }
    payload_b = {**payload_a, "kit_owner_email": "second@example.com"}

    create_a = await app_client.post("/consents", json=payload_a)
    create_b = await app_client.post("/consents", json=payload_b)
    assert create_a.status_code == 201
    assert create_b.status_code == 201
    consent_a_id = create_a.json()["id"]
    consent_b_id = create_b.json()["id"]

    # Revoke второй — он всё равно должен остаться в списке.
    revoke = await app_client.delete(f"/consents/{consent_b_id}")
    assert revoke.status_code == 204

    list_resp = await app_client.get(f"/users/{user_id}/consents")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 2
    ids = {item["id"] for item in items}
    assert ids == {consent_a_id, consent_b_id}

    by_id = {item["id"]: item for item in items}
    assert by_id[consent_a_id]["is_active"] is True
    assert by_id[consent_a_id]["revoked_at"] is None
    assert by_id[consent_b_id]["is_active"] is False
    assert by_id[consent_b_id]["revoked_at"] is not None


@pytest.mark.db
@pytest.mark.integration
async def test_list_user_consents_empty_for_unknown_user(app_client) -> None:
    """Неизвестный user_id — пустой список (200, не 404).

    Endpoint просто фильтрует — отсутствие записей не ошибка.
    """
    resp = await app_client.get(f"/users/{uuid4()}/consents")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.db
@pytest.mark.integration
async def test_list_user_consents_isolated_between_users(
    app_client, seeded_user_and_tree, postgres_dsn
) -> None:
    """Consent другого пользователя не должен попасть в чужой /users/{id}/consents."""
    import os

    from shared_models.orm import Tree, User
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    user_a_id, tree_a_id = seeded_user_and_tree

    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = os.urandom(4).hex()
    async with factory() as session, session.begin():
        user_b = User(
            email=f"isolated-{suffix}@example.com",
            external_auth_id=f"auth0|isolated-{suffix}",
            display_name="Isolated User",
        )
        session.add(user_b)
        await session.flush()
        tree_b = Tree(owner_user_id=user_b.id, name="Isolated Tree")
        session.add(tree_b)
        await session.flush()
        user_b_id, tree_b_id = user_b.id, tree_b.id
    await engine.dispose()

    create_a = await app_client.post(
        "/consents",
        json={
            "tree_id": str(tree_a_id),
            "user_id": str(user_a_id),
            "kit_owner_email": "a@example.com",
            "consent_text": "consent",
        },
    )
    create_b = await app_client.post(
        "/consents",
        json={
            "tree_id": str(tree_b_id),
            "user_id": str(user_b_id),
            "kit_owner_email": "b@example.com",
            "consent_text": "consent",
        },
    )
    assert create_a.status_code == 201
    assert create_b.status_code == 201

    list_a = await app_client.get(f"/users/{user_a_id}/consents")
    assert list_a.status_code == 200
    items_a = list_a.json()
    assert len(items_a) == 1
    assert items_a[0]["user_id"] == str(user_a_id)

    list_b = await app_client.get(f"/users/{user_b_id}/consents")
    assert list_b.status_code == 200
    items_b = list_b.json()
    assert len(items_b) == 1
    assert items_b[0]["user_id"] == str(user_b_id)
