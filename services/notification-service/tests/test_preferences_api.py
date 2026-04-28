"""End-to-end tests для preferences API (Phase 8.0 wire-up, ADR-0029).

GET /users/me/notification-preferences (defaults materialization),
PATCH /users/me/notification-preferences/{event_type} (upsert,
unknown event_type → 404, no-op body → 400, unknown channel → 400),
roundtrip: PATCH → GET reflects изменение.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


async def test_preferences_default_materialization(app_client) -> None:
    """GET без любых сохранённых prefs → строки для всех known event_type'ов."""
    response = await app_client.get(
        "/users/me/notification-preferences",
        headers={"X-User-Id": "501"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == 501

    types_in_response = {item["event_type"] for item in body["items"]}
    # Сверим минимум — самые «осевые» типы. Список расширяется со временем.
    assert "hypothesis_pending_review" in types_in_response
    assert "dna_match_found" in types_in_response
    assert "import_completed" in types_in_response

    for item in body["items"]:
        assert item["enabled"] is True  # дефолт — всё включено
        assert item["is_default"] is True
        assert "in_app" in item["channels"]


async def test_preferences_patch_persists_and_reflects(app_client) -> None:
    patch = await app_client.patch(
        "/users/me/notification-preferences/hypothesis_pending_review",
        headers={"X-User-Id": "502"},
        json={"enabled": False},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["enabled"] is False
    assert body["event_type"] == "hypothesis_pending_review"

    # GET отражает изменение и помечает is_default=False.
    response = await app_client.get(
        "/users/me/notification-preferences",
        headers={"X-User-Id": "502"},
    )
    items = {item["event_type"]: item for item in response.json()["items"]}
    target = items["hypothesis_pending_review"]
    assert target["enabled"] is False
    assert target["is_default"] is False
    # Остальные остались дефолтными.
    other = items["dna_match_found"]
    assert other["is_default"] is True


async def test_preferences_patch_unknown_event_type_404(app_client) -> None:
    response = await app_client.patch(
        "/users/me/notification-preferences/totally_made_up",
        headers={"X-User-Id": "503"},
        json={"enabled": False},
    )
    assert response.status_code == 404
    assert "totally_made_up" in response.text


async def test_preferences_patch_empty_body_400(app_client) -> None:
    response = await app_client.patch(
        "/users/me/notification-preferences/import_completed",
        headers={"X-User-Id": "504"},
        json={},
    )
    assert response.status_code == 400


async def test_preferences_patch_unknown_channel_400(app_client) -> None:
    response = await app_client.patch(
        "/users/me/notification-preferences/import_completed",
        headers={"X-User-Id": "505"},
        json={"channels": ["pigeon_post"]},
    )
    assert response.status_code == 400


async def test_preferences_patch_idempotent(app_client) -> None:
    """Второй patch с теми же данными — то же значение, без ошибок."""
    a = await app_client.patch(
        "/users/me/notification-preferences/import_failed",
        headers={"X-User-Id": "506"},
        json={"enabled": False, "channels": ["in_app"]},
    )
    b = await app_client.patch(
        "/users/me/notification-preferences/import_failed",
        headers={"X-User-Id": "506"},
        json={"enabled": False, "channels": ["in_app"]},
    )
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json() == b.json()


async def test_preferences_disable_dispatch_skipped_via_api(app_client) -> None:
    """Контракт-проверка: prefs disabled → POST /notify даёт skipped_by_pref."""
    # 1. Disable hypothesis_pending_review для user 507.
    await app_client.patch(
        "/users/me/notification-preferences/hypothesis_pending_review",
        headers={"X-User-Id": "507"},
        json={"enabled": False},
    )
    # 2. Internal POST /notify с этим event_type.
    response = await app_client.post(
        "/notify",
        json={
            "user_id": 507,
            "event_type": "hypothesis_pending_review",
            "payload": {"ref_id": "h-disabled"},
            "channels": ["in_app", "log"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["skipped_by_pref"] is True
    assert body["notification_id"] is None
    assert body["delivered"] == []

    # 3. GET /users/me/notifications не показывает эту нотификацию.
    listing = await app_client.get(
        "/users/me/notifications",
        headers={"X-User-Id": "507"},
    )
    assert listing.json()["total"] == 0


async def test_preferences_requires_x_user_id(app_client) -> None:
    response = await app_client.get("/users/me/notification-preferences")
    assert response.status_code == 401
