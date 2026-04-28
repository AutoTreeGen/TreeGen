"""Tests for POST /telegram/webhook (secret validation + dispatch)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# Должно совпадать со значением в conftest.py — duplicate intentional,
# чтобы тестовый модуль не зависел от cross-module import (нет __init__.py
# в tests/, чтобы избежать pytest plugin collision с другими пакетами).
TEST_WEBHOOK_SECRET = "x" * 32

# Минимальный валидный Telegram Update (id + message с from + chat).
_VALID_UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 1700000000,
        "chat": {"id": 100, "type": "private"},
        "from": {"id": 200, "is_bot": False, "first_name": "Test"},
        "text": "/start",
    },
}


def test_webhook_rejects_missing_secret(client: TestClient) -> None:
    resp = client.post("/telegram/webhook", json=_VALID_UPDATE)
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret(client: TestClient) -> None:
    resp = client.post(
        "/telegram/webhook",
        json=_VALID_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
    )
    assert resp.status_code == 401


def test_webhook_accepts_valid_secret_and_dispatches(
    client: TestClient,
    mock_dispatcher: MagicMock,
) -> None:
    resp = client.post(
        "/telegram/webhook",
        json=_VALID_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": TEST_WEBHOOK_SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    mock_dispatcher.feed_webhook_update.assert_awaited_once()


def test_webhook_rejects_malformed_update(client: TestClient) -> None:
    resp = client.post(
        "/telegram/webhook",
        json={"not_a_real": "update"},
        headers={"X-Telegram-Bot-Api-Secret-Token": TEST_WEBHOOK_SECRET},
    )
    # Pydantic accepts dict with extra fields by default; aiogram Update
    # требует update_id — без него 422.
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "wrong_secret",
    [
        "",
        " " * 32,
        TEST_WEBHOOK_SECRET[:-1],  # one char shorter
        TEST_WEBHOOK_SECRET + "y",  # one char longer
    ],
)
def test_webhook_rejects_close_but_invalid_secrets(
    client: TestClient,
    wrong_secret: str,
) -> None:
    resp = client.post(
        "/telegram/webhook",
        json=_VALID_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": wrong_secret},
    )
    assert resp.status_code == 401
