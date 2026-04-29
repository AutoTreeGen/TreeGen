"""Tests for POST /telegram/link/confirm (one-time-token consume + insert).

Используем ``httpx.AsyncClient(transport=ASGITransport(app=...))``,
а не ``TestClient``, потому что link-token-store на fakeredis
привязан к event-loop'у async-фикстур; синхронный TestClient ведёт
свой собственный loop и ловит ``RuntimeError: bound to a different
event loop``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Final
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared_models.orm import TelegramUserLink
from sqlalchemy.exc import IntegrityError
from telegram_bot.api import link as link_api
from telegram_bot.api import webhook as webhook_api
from telegram_bot.config import Settings, get_settings
from telegram_bot.database import get_session
from telegram_bot.main import app
from telegram_bot.services.link_tokens import LinkTokenStore

_TEST_SECRET: Final = "x" * 32


@pytest_asyncio.fixture
async def async_client(
    mock_session: MagicMock,
    link_store: LinkTokenStore,
) -> AsyncIterator[AsyncClient]:
    """ASGI client с проинжекченными overrides — на одном event-loop'е с fakeredis."""
    get_settings.cache_clear()
    s = Settings(
        webhook_secret=_TEST_SECRET,
        bot_token="0:test",
        web_base_url="https://web.test",
        link_ttl_seconds=900,
    )

    async def _session_override() -> AsyncIterator[MagicMock]:
        yield mock_session

    async def _settings_override() -> Settings:
        return s

    async def _dispatcher_override() -> MagicMock:
        dp = MagicMock()
        dp.feed_webhook_update = AsyncMock(return_value=None)
        return dp

    async def _bot_override() -> MagicMock:
        return MagicMock()

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_settings] = _settings_override
    app.dependency_overrides[webhook_api._get_dispatcher] = _dispatcher_override
    app.dependency_overrides[webhook_api._get_bot] = _bot_override
    link_api.set_link_tokens(link_store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(webhook_api._get_dispatcher, None)
    app.dependency_overrides.pop(webhook_api._get_bot, None)
    link_api.set_link_tokens(None)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_confirm_with_valid_token_inserts_link(
    async_client: AsyncClient,
    link_store: LinkTokenStore,
    mock_session: MagicMock,
) -> None:
    token = await link_store.mint(tg_chat_id=999, tg_user_id=111)
    user_id = uuid.uuid4()
    resp = await async_client.post(
        "/telegram/link/confirm",
        json={"token": token, "user_id": str(user_id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(user_id)
    assert body["tg_chat_id"] == 999
    mock_session.add.assert_called_once()
    inserted = mock_session.add.call_args.args[0]
    assert isinstance(inserted, TelegramUserLink)
    assert inserted.tg_chat_id == 999
    assert inserted.tg_user_id == 111
    assert inserted.user_id == user_id


@pytest.mark.asyncio
async def test_confirm_with_unknown_token_returns_410(
    async_client: AsyncClient,
) -> None:
    resp = await async_client.post(
        "/telegram/link/confirm",
        json={"token": "no-such-token-xxxxx", "user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_confirm_replay_returns_410(
    async_client: AsyncClient,
    link_store: LinkTokenStore,
) -> None:
    token = await link_store.mint(tg_chat_id=555, tg_user_id=666)
    user_id = uuid.uuid4()
    first = await async_client.post(
        "/telegram/link/confirm",
        json={"token": token, "user_id": str(user_id)},
    )
    assert first.status_code == 200
    second = await async_client.post(
        "/telegram/link/confirm",
        json={"token": token, "user_id": str(user_id)},
    )
    assert second.status_code == 410


@pytest.mark.asyncio
async def test_confirm_when_chat_already_linked_to_other_user(
    async_client: AsyncClient,
    link_store: LinkTokenStore,
    mock_session: MagicMock,
) -> None:
    token = await link_store.mint(tg_chat_id=42, tg_user_id=43)
    other_user = uuid.uuid4()
    existing = MagicMock(spec=TelegramUserLink)
    existing.user_id = other_user
    existing.tg_chat_id = 42
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=existing)
    mock_session.execute.return_value = result

    new_user = uuid.uuid4()
    resp = await async_client.post(
        "/telegram/link/confirm",
        json={"token": token, "user_id": str(new_user)},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_confirm_idempotent_for_same_user(
    async_client: AsyncClient,
    link_store: LinkTokenStore,
    mock_session: MagicMock,
) -> None:
    token = await link_store.mint(tg_chat_id=42, tg_user_id=43)
    user_id = uuid.uuid4()
    existing = MagicMock(spec=TelegramUserLink)
    existing.id = uuid.uuid4()
    existing.user_id = user_id
    existing.tg_chat_id = 42
    existing.linked_at = MagicMock()
    existing.linked_at.isoformat = MagicMock(return_value="2026-04-29T00:00:00+00:00")
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=existing)
    mock_session.execute.return_value = result

    resp = await async_client.post(
        "/telegram/link/confirm",
        json={"token": token, "user_id": str(user_id)},
    )
    assert resp.status_code == 200
    mock_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_handles_concurrent_insert_race(
    async_client: AsyncClient,
    link_store: LinkTokenStore,
    mock_session: MagicMock,
) -> None:
    token = await link_store.mint(tg_chat_id=42, tg_user_id=43)
    mock_session.flush.side_effect = IntegrityError("uq", {}, Exception("dup"))
    user_id = uuid.uuid4()
    resp = await async_client.post(
        "/telegram/link/confirm",
        json={"token": token, "user_id": str(user_id)},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_confirm_rejects_short_token(async_client: AsyncClient) -> None:
    resp = await async_client.post(
        "/telegram/link/confirm",
        json={"token": "short", "user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422
