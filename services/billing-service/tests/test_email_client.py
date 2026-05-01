"""Тесты ``send_email_async`` через httpx.MockTransport.

Не поднимаем real email-service: проверяем что billing-service POST'ит
правильный payload + не raise'ит на 5xx (best-effort delivery).
"""

from __future__ import annotations

import json
from typing import Final

import httpx
import pytest
from billing_service.config import Settings
from billing_service.services.email_client import send_email_async

# Сохраняем оригинальный класс — иначе monkeypatch + factory вызывают
# самих себя (RecursionError).
_REAL_ASYNC_CLIENT: Final = httpx.AsyncClient


def _patch_transport(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Заменить httpx.AsyncClient на factory, всегда подсовывающий transport."""

    def _factory(*_args: object, **kwargs: object) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(
        "billing_service.services.email_client.httpx.AsyncClient",
        _factory,
    )


def _settings_with(url: str) -> Settings:
    """Минимальный Settings с переопределённым email_service_url."""
    return Settings(email_service_url=url)


@pytest.mark.asyncio
async def test_send_email_skips_when_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если URL пустой — POST не делается, exception не бросается."""

    def _explode(_request: httpx.Request) -> httpx.Response:
        msg = "should not be called when email_service_url is empty"
        raise AssertionError(msg)

    _patch_transport(monkeypatch, httpx.MockTransport(_explode))

    await send_email_async(_settings_with(""), {"kind": "payment_succeeded"})


@pytest.mark.asyncio
async def test_send_email_posts_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path — POST с правильным payload'ом и path'ом ``/email/send``."""
    captured: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(202, json={"status": "queued"})

    _patch_transport(monkeypatch, httpx.MockTransport(_handler))

    payload = {
        "kind": "payment_succeeded",
        "to_user_id": "00000000-0000-0000-0000-000000000001",
        "idempotency_key": "evt_abc123",
    }
    await send_email_async(_settings_with("http://email-service:8000"), payload)

    assert captured["url"] == "http://email-service:8000/email/send"
    assert captured["body"] == payload


@pytest.mark.asyncio
async def test_send_email_swallows_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx response → warning log, без raise (best-effort)."""
    _patch_transport(
        monkeypatch,
        httpx.MockTransport(lambda _request: httpx.Response(503, text="email-service down")),
    )

    await send_email_async(
        _settings_with("http://email-service:8000"),
        {"kind": "payment_failed", "idempotency_key": "evt_x"},
    )


@pytest.mark.asyncio
async def test_send_email_swallows_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network error → warning log, без raise."""

    def _raise(_request: httpx.Request) -> httpx.Response:
        msg = "boom"
        raise httpx.ConnectError(msg)

    _patch_transport(monkeypatch, httpx.MockTransport(_raise))

    await send_email_async(
        _settings_with("http://email-service:8000"),
        {"kind": "payment_succeeded", "idempotency_key": "evt_y"},
    )
