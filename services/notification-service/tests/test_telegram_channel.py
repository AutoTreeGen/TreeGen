"""Unit tests for TelegramChannel (Phase 14.1, ADR-0056).

httpx.MockTransport имитирует bot's ``/telegram/notify``. Testcontainers
не нужны — channel'у нужна только Notification ORM-row (можно собрать в
памяти без сессии) и httpx-client.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from notification_service.channels.telegram import TelegramChannel
from notification_service.config import Settings
from shared_models.orm import Notification


def _notification(payload: dict[str, Any] | None = None) -> Notification:
    """Соберём Notification-row in-memory, без БД-сессии."""
    return Notification(
        id=uuid.uuid4(),
        user_id=12345,
        event_type="dna_match_found",
        payload=payload or {},
        idempotency_key="12345:dna_match_found:r1",
        channels_attempted=[],
    )


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    """Override settings без env-переменных, чтобы lru_cache не мешал."""
    base = {
        "telegram_bot_url": "http://bot.test",
        "telegram_internal_token": "shared-secret",
        "telegram_request_timeout_seconds": 5.0,
    }
    base.update(overrides)
    monkeypatch.setattr(
        "notification_service.channels.telegram.get_settings",
        lambda: Settings(**base),
    )


@pytest.mark.asyncio
async def test_send_skips_when_bot_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, telegram_bot_url="")
    channel = TelegramChannel()
    notif = _notification({"telegram_user_id": str(uuid.uuid4())})
    assert await channel.send(notif) is False


@pytest.mark.asyncio
async def test_send_skips_when_token_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, telegram_internal_token="")
    channel = TelegramChannel()
    notif = _notification({"telegram_user_id": str(uuid.uuid4())})
    assert await channel.send(notif) is False


@pytest.mark.asyncio
async def test_send_skips_when_payload_missing_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)
    transport = httpx.MockTransport(lambda _: pytest.fail("bot must not be called"))
    channel = TelegramChannel(http_client=httpx.AsyncClient(transport=transport))
    notif = _notification(payload={})  # no telegram_user_id
    assert await channel.send(notif) is False


@pytest.mark.asyncio
async def test_send_returns_true_when_bot_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"delivered": True, "reason": None})

    transport = httpx.MockTransport(handler)
    channel = TelegramChannel(http_client=httpx.AsyncClient(transport=transport))
    user_uuid = str(uuid.uuid4())
    notif = _notification({"telegram_user_id": user_uuid, "match_id": "abc"})
    assert await channel.send(notif) is True

    assert captured["url"] == "http://bot.test/telegram/notify"
    assert captured["headers"]["x-internal-service-token"] == "shared-secret"
    assert captured["body"]["user_id"] == user_uuid
    # Format: [event_type] key=value, key=value (sorted by key, no UUID).
    assert captured["body"]["message"].startswith("[dna_match_found]")
    assert "match_id=abc" in captured["body"]["message"]


@pytest.mark.asyncio
async def test_send_returns_false_when_bot_says_not_subscribed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"delivered": False, "reason": "not_subscribed"})

    transport = httpx.MockTransport(handler)
    channel = TelegramChannel(http_client=httpx.AsyncClient(transport=transport))
    notif = _notification({"telegram_user_id": str(uuid.uuid4())})
    assert await channel.send(notif) is False


@pytest.mark.asyncio
async def test_send_returns_false_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)

    def handler(_: httpx.Request) -> httpx.Response:
        msg = "bot unreachable"
        raise httpx.ConnectError(msg)

    transport = httpx.MockTransport(handler)
    channel = TelegramChannel(http_client=httpx.AsyncClient(transport=transport))
    notif = _notification({"telegram_user_id": str(uuid.uuid4())})
    # Не должно raise — channel ловит и возвращает False.
    assert await channel.send(notif) is False


@pytest.mark.asyncio
async def test_send_returns_false_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    transport = httpx.MockTransport(handler)
    channel = TelegramChannel(http_client=httpx.AsyncClient(transport=transport))
    notif = _notification({"telegram_user_id": str(uuid.uuid4())})
    assert await channel.send(notif) is False
