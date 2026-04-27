"""Базовые smoke-тесты consent CRUD endpoints (Task 3 scope).

Глубокие flow-тесты (revoke deletes blobs, cross-consent enforcement)
живут в test_consents_flow.py (Task 4).
"""

from __future__ import annotations

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
