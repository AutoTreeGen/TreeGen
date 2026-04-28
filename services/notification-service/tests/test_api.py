"""End-to-end API tests through ASGITransport.

POST /notify (idempotency, unknown event_type, unknown channel),
GET /users/me/notifications (auth header, unread filter, pagination),
PATCH /notifications/{id}/read (idempotent, 404 on cross-user).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


async def test_notify_creates_and_dedupes(app_client) -> None:
    body = {
        "user_id": 11,
        "event_type": "hypothesis_pending_review",
        "payload": {"ref_id": "h-100", "hypothesis_id": 100},
        "channels": ["in_app", "log"],
    }
    first = await app_client.post("/notify", json=body)
    assert first.status_code == 201, first.text
    p1 = first.json()
    assert sorted(p1["delivered"]) == ["in_app", "log"]
    assert p1["deduplicated"] is False
    notification_id = p1["notification_id"]

    # Re-send → dedupe, тот же id, deduplicated=True.
    second = await app_client.post("/notify", json=body)
    assert second.status_code == 201
    p2 = second.json()
    assert p2["notification_id"] == notification_id
    assert p2["deduplicated"] is True


async def test_notify_unknown_event_type_400(app_client) -> None:
    response = await app_client.post(
        "/notify",
        json={
            "user_id": 12,
            "event_type": "definitely_not_real",
            "payload": {"ref_id": "x"},
            "channels": ["in_app"],
        },
    )
    assert response.status_code == 400
    assert "definitely_not_real" in response.text


async def test_notify_unknown_channel_400(app_client) -> None:
    response = await app_client.post(
        "/notify",
        json={
            "user_id": 13,
            "event_type": "import_completed",
            "payload": {"ref_id": "imp-x"},
            "channels": ["smoke_signal"],
        },
    )
    assert response.status_code == 400


async def test_user_notifications_filter_and_unread_count(app_client) -> None:
    # Подгрузим две нотификации для user 21.
    for ref in ("a", "b"):
        await app_client.post(
            "/notify",
            json={
                "user_id": 21,
                "event_type": "import_completed",
                "payload": {"ref_id": f"imp-{ref}"},
                "channels": ["in_app"],
            },
        )

    response = await app_client.get(
        "/users/me/notifications",
        headers={"X-User-Id": "21"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == 21
    assert body["total"] == 2
    assert body["unread"] == 2
    assert len(body["items"]) == 2

    # Помечаем одну как прочитанную → unread сдвигается.
    target_id = body["items"][0]["id"]
    patched = await app_client.patch(
        f"/notifications/{target_id}/read",
        headers={"X-User-Id": "21"},
    )
    assert patched.status_code == 200
    assert patched.json()["id"] == target_id

    after_response = await app_client.get(
        "/users/me/notifications?unread=true",
        headers={"X-User-Id": "21"},
    )
    after = after_response.json()
    assert after["unread"] == 1
    assert all(item["read_at"] is None for item in after["items"])
    assert all(item["id"] != target_id for item in after["items"])


async def test_user_notifications_requires_x_user_id(app_client) -> None:
    response = await app_client.get("/users/me/notifications")
    assert response.status_code == 401, f"unexpected: {response.status_code} {response.text}"


async def test_mark_read_404_on_cross_user(app_client) -> None:
    """404 если запись существует, но принадлежит другому user'у."""
    create = await app_client.post(
        "/notify",
        json={
            "user_id": 31,
            "event_type": "merge_undone",
            "payload": {"ref_id": "u-1"},
            "channels": ["in_app"],
        },
    )
    notification_id = create.json()["notification_id"]

    # Другой пользователь пытается пометить — 404 (не утечь "exists for X").
    response = await app_client.patch(
        f"/notifications/{notification_id}/read",
        headers={"X-User-Id": "32"},
    )
    assert response.status_code == 404


async def test_mark_read_is_idempotent(app_client) -> None:
    create = await app_client.post(
        "/notify",
        json={
            "user_id": 41,
            "event_type": "dedup_suggestion_new",
            "payload": {"ref_id": "d-1"},
            "channels": ["in_app"],
        },
    )
    nid = create.json()["notification_id"]
    first = await app_client.patch(
        f"/notifications/{nid}/read",
        headers={"X-User-Id": "41"},
    )
    second = await app_client.patch(
        f"/notifications/{nid}/read",
        headers={"X-User-Id": "41"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    # Read_at не должно меняться при повторном patch'е.
    assert first.json()["read_at"] == second.json()["read_at"]
