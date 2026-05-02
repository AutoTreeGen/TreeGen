"""Фикстуры для registry endpoint-тестов (Phase 22.1).

Тесты гонят сквозь FastAPI без поднятой Postgres: ``repo.list_archives``
и компания подменяются на in-memory варианты через monkeypatch.
``get_session`` зашунтирован на no-op factory (роутер не использует
session напрямую, всё через repo).

Naum Katz fixture — анти-regress: SBU oblast Lviv должен находиться
запросом ``country=UA, record_type=passport_internal, year_from=1900,
year_to=1950`` (origin owner's $100 case).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _build_listing(**overrides: object) -> dict[str, Any]:
    """Канонический listing-dict, перебивается kwargs."""
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Generic archive",
        "name_native": None,
        "country": "UA",
        "region": None,
        "address": None,
        "contact_email": None,
        "contact_phone": None,
        "website": None,
        "languages": ["uk"],
        "record_types": ["civil_birth"],
        "year_from": 1850,
        "year_to": 1950,
        "access_mode": "paid_request",
        "fee_min_usd": None,
        "fee_max_usd": None,
        "typical_response_days": None,
        "privacy_window_years": None,
        "notes": None,
        "last_verified": dt.date(2026, 5, 3),
        "created_at": dt.datetime(2026, 5, 3, 12, 0, tzinfo=dt.UTC),
        "updated_at": dt.datetime(2026, 5, 3, 12, 0, tzinfo=dt.UTC),
    }
    base.update(overrides)
    return base


class _FakeListing:
    """Дублирует ORM-API минимально нужный роутером (``to_dict`` + аттрибуты).

    Не наследуем ORM, чтобы тесты не требовали engine для Mapped-init.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        for key, value in data.items():
            setattr(self, key, value)
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


@pytest.fixture
def fake_listings() -> list[_FakeListing]:
    """5 представительных listing-ов для UA / RU / PL / DE / GB.

    Включает SBU Lviv для Naum Katz сценария.
    """
    return [
        _FakeListing(
            _build_listing(
                name="SBU oblast archive Lviv",
                country="UA",
                region="Lviv oblast",
                record_types=["passport_internal", "nkvd_kgb_file"],
                year_from=1939,
                year_to=1991,
                access_mode="paid_request",
                fee_min_usd=50,
                fee_max_usd=150,
                privacy_window_years=75,
                languages=["uk", "ru"],
            )
        ),
        _FakeListing(
            _build_listing(
                name="DAZHO — Zhytomyr oblast archive",
                country="UA",
                region="Zhytomyr oblast",
                record_types=["civil_birth", "civil_marriage", "metric_book"],
                year_from=1750,
                year_to=1933,
                access_mode="paid_request",
            )
        ),
        _FakeListing(
            _build_listing(
                name="TsAMO Podolsk",
                country="RU",
                region="Moscow oblast",
                record_types=["military"],
                year_from=1941,
                year_to=1991,
                access_mode="paid_request",
            )
        ),
        _FakeListing(
            _build_listing(
                name="AGAD Warsaw",
                country="PL",
                region="Warsaw",
                record_types=["metric_book", "revision_list", "notarial"],
                year_from=1100,
                year_to=1918,
                access_mode="in_person_only",
            )
        ),
        _FakeListing(
            _build_listing(
                name="Standesamt Berlin Mitte",
                country="DE",
                region="Berlin",
                record_types=["civil_birth", "civil_marriage", "civil_death"],
                year_from=1874,
                year_to=None,
                access_mode="paid_request",
                privacy_window_years=110,
            )
        ),
    ]


@pytest.fixture
def patch_repo(
    monkeypatch: pytest.MonkeyPatch,
    fake_listings: list[_FakeListing],
) -> dict[str, Any]:
    """Подменяет repo.* на in-memory variants поверх ``fake_listings``.

    Возвращает mutable dict (state holder), чтобы тесты могли:

    * добавлять/удалять записи (CRUD-тесты)
    * проверять сделанные mutations
    """
    state: dict[str, _FakeListing] = {str(item.id): item for item in fake_listings}

    async def _list_archives(
        _session: object,
        *,
        country: str | None = None,
        record_type: str | None = None,
    ) -> list[_FakeListing]:
        items = list(state.values())
        if country:
            items = [it for it in items if it.country == country]
        if record_type:
            items = [it for it in items if record_type in (it.record_types or [])]
        return items

    async def _get_archive(_session: object, listing_id: uuid.UUID) -> _FakeListing | None:
        return state.get(str(listing_id))

    async def _create_archive(_session: object, payload: dict[str, Any]) -> _FakeListing:
        full = _build_listing(**payload)
        listing = _FakeListing(full)
        state[str(listing.id)] = listing
        return listing

    async def _update_archive(
        _session: object,
        listing_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> _FakeListing | None:
        existing = state.get(str(listing_id))
        if existing is None:
            return None
        merged = existing.to_dict()
        merged.update(payload)
        new_listing = _FakeListing(merged)
        state[str(listing_id)] = new_listing
        return new_listing

    async def _delete_archive(_session: object, listing_id: uuid.UUID) -> bool:
        return state.pop(str(listing_id), None) is not None

    from archive_service.registry import repo

    monkeypatch.setattr(repo, "list_archives", _list_archives)
    monkeypatch.setattr(repo, "get_archive", _get_archive)
    monkeypatch.setattr(repo, "create_archive", _create_archive)
    monkeypatch.setattr(repo, "update_archive", _update_archive)
    monkeypatch.setattr(repo, "delete_archive", _delete_archive)
    return {"state": state}


@pytest_asyncio.fixture
async def registry_client(
    monkeypatch: pytest.MonkeyPatch,
    patch_repo: dict[str, Any],  # noqa: ARG001 — side-effect-only.
) -> AsyncIterator[AsyncClient]:
    """TestClient с overridden Clerk-claims; auth = anonymous user (sub=u_test)."""
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    monkeypatch.setenv("RATE_LIMITING_ENABLED", "false")
    monkeypatch.setenv("ARCHIVE_SERVICE_ADMIN_EMAIL", "owner@autotreegen.local")

    from archive_service.auth import get_current_claims
    from archive_service.config import get_settings
    from archive_service.database import get_session
    from archive_service.main import app

    # get_session не используется роутером (всё через monkeypatched repo),
    # но FastAPI всё равно резолвит dependency — даём no-op stub.
    async def _stub_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(
        sub="u_test",
        email=None,
    )
    # Reset settings cache, чтобы admin_email подхватился из ENV.
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def registry_client_admin(
    monkeypatch: pytest.MonkeyPatch,
    patch_repo: dict[str, Any],  # noqa: ARG001 — side-effect-only.
) -> AsyncIterator[AsyncClient]:
    """TestClient авторизован как admin (claims.email == settings.admin_email)."""
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    monkeypatch.setenv("RATE_LIMITING_ENABLED", "false")
    monkeypatch.setenv("ARCHIVE_SERVICE_ADMIN_EMAIL", "owner@autotreegen.local")

    from archive_service.auth import get_current_claims
    from archive_service.database import get_session
    from archive_service.main import app

    async def _stub_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(
        sub="u_owner",
        email="owner@autotreegen.local",
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


_ListingBuilder = Callable[..., dict[str, Any]]


@pytest.fixture
def make_listing_payload() -> _ListingBuilder:
    """Helper для CRUD-тестов: минимально-валидный POST body."""

    def _build(**overrides: object) -> dict[str, Any]:
        base: dict[str, Any] = {
            "name": "Test archive (CRUD)",
            "country": "UA",
            "region": "Test oblast",
            "languages": ["uk"],
            "record_types": ["civil_birth"],
            "year_from": 1900,
            "year_to": 1930,
            "access_mode": "paid_request",
            "last_verified": "2026-05-03",
        }
        base.update(overrides)
        return base

    return _build
