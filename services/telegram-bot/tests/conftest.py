"""Test fixtures для telegram-bot.

Никаких сетевых вызовов: ``api.telegram.org`` не зовётся. БД не
поднимается (используем mocked AsyncSession). Redis — fakeredis.
TestClient без ``with`` — lifespan не запускается, deps инжектируем
вручную.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from telegram_bot.api import link as link_api
from telegram_bot.api import webhook as webhook_api
from telegram_bot.config import Settings, get_settings
from telegram_bot.database import get_session
from telegram_bot.main import app
from telegram_bot.services.link_tokens import LinkTokenStore

TEST_WEBHOOK_SECRET = "x" * 32


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Settings с фиксированным webhook_secret и пустым bot_token."""
    get_settings.cache_clear()
    s = Settings(
        webhook_secret=TEST_WEBHOOK_SECRET,
        bot_token="0:test",
        web_base_url="https://web.test",
        link_ttl_seconds=900,
    )

    def _override() -> Settings:
        return s

    app.dependency_overrides[get_settings] = _override
    yield s
    app.dependency_overrides.pop(get_settings, None)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Fakeredis async-клиент."""
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def link_store(fake_redis: fakeredis.aioredis.FakeRedis) -> LinkTokenStore:
    """LinkTokenStore поверх fakeredis."""
    return LinkTokenStore(fake_redis, ttl_seconds=900)


@pytest.fixture
def mock_session() -> MagicMock:
    """Mocked AsyncSession — отслеживает .add(...) и .flush(...).

    По умолчанию `execute(...)` возвращает `scalar_one_or_none() == None`
    (нет существующего линка). Тесты могут переопределить.
    """
    session = MagicMock(name="AsyncSession")
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture
def mock_dispatcher() -> MagicMock:
    """Mocked aiogram Dispatcher — feed_webhook_update — AsyncMock."""
    dp = MagicMock(name="Dispatcher")
    dp.feed_webhook_update = AsyncMock(return_value=None)
    return dp


@pytest.fixture
def mock_bot() -> MagicMock:
    """Mocked aiogram Bot — без реальной HTTP-сессии."""
    return MagicMock(name="Bot")


@pytest.fixture
def client(
    settings: Settings,  # noqa: ARG001
    mock_session: MagicMock,
    mock_dispatcher: MagicMock,
    mock_bot: MagicMock,
    link_store: LinkTokenStore,
) -> Iterator[TestClient]:
    """FastAPI TestClient с проинжекченными overrides (без lifespan)."""

    async def _session_override() -> AsyncIterator[MagicMock]:
        yield mock_session

    async def _dispatcher_override() -> MagicMock:
        return mock_dispatcher

    async def _bot_override() -> MagicMock:
        return mock_bot

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[webhook_api._get_dispatcher] = _dispatcher_override
    app.dependency_overrides[webhook_api._get_bot] = _bot_override
    link_api.set_link_tokens(link_store)
    try:
        c = TestClient(app)
        yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(webhook_api._get_dispatcher, None)
        app.dependency_overrides.pop(webhook_api._get_bot, None)
        link_api.set_link_tokens(None)


def make_test_token() -> str:
    """Test helper — фиксированный валидный токен ≥16 символов."""
    return "test-token-" + ("x" * 20)
