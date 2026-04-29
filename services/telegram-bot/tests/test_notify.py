"""Tests for POST /telegram/notify (Phase 14.1, ADR-0056).

Endpoint имеет три ветви:
* 503 если ``internal_service_token`` пустой;
* 401 если header не совпадает;
* 200 + ``delivered=True`` happy path (mocked Bot.send_message);
* 200 + ``delivered=False`` (no link / unsubscribed) — без exception'а.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Final
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared_models.orm import TelegramUserLink
from telegram_bot.api import link as link_api
from telegram_bot.api import notify as notify_api
from telegram_bot.api import webhook as webhook_api
from telegram_bot.config import Settings, get_settings
from telegram_bot.database import get_session
from telegram_bot.main import app
from telegram_bot.services.link_tokens import LinkTokenStore

_TEST_SECRET: Final = "y" * 32
_TEST_INTERNAL_TOKEN: Final = "z" * 32


def _link_row(
    *,
    user_id: uuid.UUID,
    tg_chat_id: int,
    notifications_enabled: bool = True,
    revoked: bool = False,
) -> TelegramUserLink:
    return TelegramUserLink(
        id=uuid.uuid4(),
        user_id=user_id,
        tg_chat_id=tg_chat_id,
        tg_user_id=tg_chat_id + 1,
        linked_at=dt.datetime.now(dt.UTC),
        revoked_at=dt.datetime.now(dt.UTC) if revoked else None,
        notifications_enabled=notifications_enabled,
    )


@pytest_asyncio.fixture
async def async_client_factory(
    mock_session: MagicMock,
    link_store: LinkTokenStore,
) -> AsyncIterator[object]:
    """Фабрика клиентов с настраиваемым internal_service_token и Bot mock'ом."""
    get_settings.cache_clear()
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock(return_value=None)

    settings_holder = {
        "settings": Settings(
            webhook_secret=_TEST_SECRET,
            bot_token="0:test",
            web_base_url="https://web.test",
            internal_service_token=_TEST_INTERNAL_TOKEN,
        ),
    }

    async def _session_override() -> AsyncIterator[MagicMock]:
        yield mock_session

    async def _settings_override() -> Settings:
        return settings_holder["settings"]

    async def _dispatcher_override() -> MagicMock:
        dp = MagicMock()
        dp.feed_webhook_update = AsyncMock(return_value=None)
        return dp

    async def _bot_override() -> MagicMock:
        return bot_mock

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_settings] = _settings_override
    app.dependency_overrides[webhook_api._get_dispatcher] = _dispatcher_override
    app.dependency_overrides[webhook_api._get_bot] = _bot_override
    app.dependency_overrides[notify_api._get_bot] = _bot_override
    link_api.set_link_tokens(link_store)

    transport = ASGITransport(app=app)

    async def factory(*, internal_token: str | None = _TEST_INTERNAL_TOKEN) -> AsyncClient:
        settings_holder["settings"] = Settings(
            webhook_secret=_TEST_SECRET,
            bot_token="0:test",
            web_base_url="https://web.test",
            internal_service_token=internal_token or "",
        )
        return AsyncClient(transport=transport, base_url="http://test")

    yield {"factory": factory, "bot": bot_mock, "session": mock_session}

    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(webhook_api._get_dispatcher, None)
    app.dependency_overrides.pop(webhook_api._get_bot, None)
    app.dependency_overrides.pop(notify_api._get_bot, None)
    link_api.set_link_tokens(None)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_notify_503_when_internal_token_unset(async_client_factory) -> None:
    client = await async_client_factory["factory"](internal_token="")
    async with client:
        resp = await client.post(
            "/telegram/notify",
            json={"user_id": str(uuid.uuid4()), "message": "hi"},
            headers={"X-Internal-Service-Token": "anything"},
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_notify_401_when_header_missing(async_client_factory) -> None:
    client = await async_client_factory["factory"]()
    async with client:
        resp = await client.post(
            "/telegram/notify",
            json={"user_id": str(uuid.uuid4()), "message": "hi"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_notify_401_when_header_mismatch(async_client_factory) -> None:
    client = await async_client_factory["factory"]()
    async with client:
        resp = await client.post(
            "/telegram/notify",
            json={"user_id": str(uuid.uuid4()), "message": "hi"},
            headers={"X-Internal-Service-Token": "wrong-token-xxxx"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_notify_returns_delivered_false_when_no_link(async_client_factory) -> None:
    # mock_session.execute().scalar_one_or_none() = None (default fixture)
    client = await async_client_factory["factory"]()
    async with client:
        resp = await client.post(
            "/telegram/notify",
            json={"user_id": str(uuid.uuid4()), "message": "hi"},
            headers={"X-Internal-Service-Token": _TEST_INTERNAL_TOKEN},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is False
    assert body["reason"] == "no_active_link"
    async_client_factory["bot"].send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_delivered_false_when_unsubscribed(async_client_factory) -> None:
    user_id = uuid.uuid4()
    link = _link_row(user_id=user_id, tg_chat_id=42, notifications_enabled=False)
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=link)
    async_client_factory["session"].execute = AsyncMock(return_value=res)

    client = await async_client_factory["factory"]()
    async with client:
        resp = await client.post(
            "/telegram/notify",
            json={"user_id": str(user_id), "message": "hi"},
            headers={"X-Internal-Service-Token": _TEST_INTERNAL_TOKEN},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is False
    assert body["reason"] == "not_subscribed"
    async_client_factory["bot"].send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_happy_path_calls_bot(async_client_factory) -> None:
    user_id = uuid.uuid4()
    link = _link_row(user_id=user_id, tg_chat_id=4242, notifications_enabled=True)
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=link)
    async_client_factory["session"].execute = AsyncMock(return_value=res)

    client = await async_client_factory["factory"]()
    async with client:
        resp = await client.post(
            "/telegram/notify",
            json={"user_id": str(user_id), "message": "Hello"},
            headers={"X-Internal-Service-Token": _TEST_INTERNAL_TOKEN},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is True
    bot = async_client_factory["bot"]
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 4242
    assert kwargs["text"] == "Hello"
