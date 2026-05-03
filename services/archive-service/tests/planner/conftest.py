"""Фикстуры планировщика: TestClient + Clerk auth + helpers.

Планировщик в проде ходит в БД через ``Depends(get_session)`` →
``fetch_undocumented_events``. В тестах подменяем верхнеуровневый
``get_events_fetcher`` через фикстуру ``override_fetcher`` — БД не нужна.

Helpers (``make_event``, ``override_fetcher``, фиксированные UUID) — фикстуры,
а не свободные импорты, потому что pytest сконфигурирован с
``--import-mode=importlib`` (см. корневой ``pyproject.toml``), и кросс-импорты
между test-файлами работают только через conftest fixtures.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Final
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from archive_service.planner.dto import UndocumentedEvent
from archive_service.planner.repo import EventsFetcher
from httpx import ASGITransport, AsyncClient

# Фиксированные UUID-ы — экспортируем как фикстуры ниже.
_PERSON_ID: Final[uuid.UUID] = uuid.UUID("11111111-1111-4111-8111-111111111111")
_EVENT_ID_LODZ_BIRTH: Final[uuid.UUID] = uuid.UUID("22222222-2222-4222-8222-222222222222")
_EVENT_ID_WARSAW_DEATH: Final[uuid.UUID] = uuid.UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture
def person_id() -> uuid.UUID:
    return _PERSON_ID


@pytest.fixture
def event_id_lodz() -> uuid.UUID:
    return _EVENT_ID_LODZ_BIRTH


@pytest.fixture
def event_id_warsaw() -> uuid.UUID:
    return _EVENT_ID_WARSAW_DEATH


EventBuilder = Callable[..., UndocumentedEvent]


@pytest.fixture
def make_event() -> EventBuilder:
    """Фабрика ``UndocumentedEvent`` с keyword-only аргументами."""

    def _make_event(
        *,
        event_id: uuid.UUID,
        event_type: str,
        year: int | None,
        country: str | None,
        city: str | None,
    ) -> UndocumentedEvent:
        date = dt.date(year, 1, 1) if year is not None else None
        return UndocumentedEvent(
            event_id=event_id,
            event_type=event_type,
            date_start=date,
            date_end=date,
            place_country_iso=country,
            place_city=city,
        )

    return _make_event


def _make_fetcher(events: list[UndocumentedEvent]) -> EventsFetcher:
    async def _fetch(_person_id: uuid.UUID) -> list[UndocumentedEvent]:
        return events

    return _fetch


@pytest.fixture
def override_fetcher() -> Callable[[list[UndocumentedEvent]], None]:
    """Установить мок ``get_events_fetcher`` на заданный список событий.

    Использование:
        def test_x(planner_client, override_fetcher, make_event):
            override_fetcher([make_event(...)])
            r = await planner_client.get(...)
    """
    from archive_service.main import app
    from archive_service.planner.router import get_events_fetcher

    def _override(events: list[UndocumentedEvent]) -> None:
        fetcher = _make_fetcher(events)
        app.dependency_overrides[get_events_fetcher] = lambda: fetcher

    return _override


@pytest_asyncio.fixture
async def planner_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """TestClient с overridden Clerk-claims; planner-fetcher НЕ замокан.

    Тест вызывает ``override_fetcher(events)`` для подстановки данных.
    """
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    monkeypatch.setenv("RATE_LIMITING_ENABLED", "false")

    from archive_service.auth import get_current_claims
    from archive_service.main import app

    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="u_test")

    # Phase 15.11c: planner endpoint теперь зависит от sealed-scopes
    # fetcher'а. По умолчанию в тестах никаких active assertions нет,
    # поэтому возвращаем empty frozenset — это устраняет необходимость
    # инициализировать engine в тестах, которые не трогают БД.
    from archive_service.planner.router import get_sealed_scopes_fetcher

    async def _empty_sealed(_person_id: uuid.UUID) -> frozenset:
        return frozenset()

    app.dependency_overrides[get_sealed_scopes_fetcher] = lambda: _empty_sealed
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
