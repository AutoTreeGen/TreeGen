"""End-to-end тесты для GET /archive-planner/persons/{id}/suggestions.

Helpers (``make_event``, ``override_fetcher``, фиксированные UUID) приходят
через conftest-фикстуры — pytest importlib-mode не позволяет прямые
кросс-импорты test-файлов.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from archive_service.planner.dto import UndocumentedEvent
from httpx import AsyncClient

EventBuilder = Callable[..., UndocumentedEvent]
FetcherOverride = Callable[[list[UndocumentedEvent]], None]


@pytest.mark.asyncio
async def test_lodz_birth_returns_lodz_archive(
    planner_client: AsyncClient,
    override_fetcher: FetcherOverride,
    make_event: EventBuilder,
    person_id: uuid.UUID,
    event_id_lodz: uuid.UUID,
) -> None:
    """Birth in Łódź 1880, no source → suggestions включают pl-aplodz-vital."""
    override_fetcher(
        [
            make_event(
                event_id=event_id_lodz,
                event_type="BIRT",
                year=1880,
                country="PL",
                city="Łódź",
            ),
        ],
    )
    r = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["person_id"] == str(person_id)
    assert body["undocumented_event_count"] == 1
    archive_ids = [s["archive_id"] for s in body["suggestions"]]
    assert "pl-aplodz-vital" in archive_ids
    for s in body["suggestions"]:
        assert s["related_event_id"] == str(event_id_lodz)
        assert s["related_event_type"] == "BIRT"
    # Łódź vital — в топ-3 (PL country + city match).
    assert "pl-aplodz-vital" in archive_ids[:3]


@pytest.mark.asyncio
async def test_no_undocumented_events_returns_empty(
    planner_client: AsyncClient,
    override_fetcher: FetcherOverride,
    person_id: uuid.UUID,
) -> None:
    """Все события задокументированы (fetcher → []) → пустой список + count=0."""
    override_fetcher([])
    r = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["undocumented_event_count"] == 0
    assert body["suggestions"] == []


@pytest.mark.asyncio
async def test_locale_ru_changes_ranking(
    planner_client: AsyncClient,
    override_fetcher: FetcherOverride,
    make_event: EventBuilder,
    person_id: uuid.UUID,
    event_id_lodz: uuid.UUID,
) -> None:
    """С locale=ru архив с 'ru' в languages обгоняет архив без 'ru'.

    Сравниваем pl-aplodz-vital (ru ∈ langs) vs pl-apkrakow (без ru):
    оба покрывают PL+1880, но Łódź совпадает по городу.
    """
    override_fetcher(
        [
            make_event(
                event_id=event_id_lodz,
                event_type="BIRT",
                year=1880,
                country="PL",
                city="Łódź",
            ),
        ],
    )
    r_ru = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        params={"locale": "ru", "limit": 25},
        headers={"Authorization": "Bearer fake"},
    )
    assert r_ru.status_code == 200
    by_id_ru = {s["archive_id"]: s["priority_score"] for s in r_ru.json()["suggestions"]}

    r_en = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        params={"locale": "en", "limit": 25},
        headers={"Authorization": "Bearer fake"},
    )
    assert r_en.status_code == 200
    by_id_en = {s["archive_id"]: s["priority_score"] for s in r_en.json()["suggestions"]}

    # pl-aplodz-vital (ru ∈ langs) — выше в ru, чем в en.
    assert by_id_ru["pl-aplodz-vital"] > by_id_en["pl-aplodz-vital"]
    # pl-apkrakow (нет ru) — не сдвинулся.
    assert by_id_ru["pl-apkrakow"] == by_id_en["pl-apkrakow"]


@pytest.mark.asyncio
async def test_response_pydantic_shape(
    planner_client: AsyncClient,
    override_fetcher: FetcherOverride,
    make_event: EventBuilder,
    person_id: uuid.UUID,
    event_id_warsaw: uuid.UUID,
) -> None:
    """Все обязательные поля присутствуют, типы совпадают со схемой."""
    override_fetcher(
        [
            make_event(
                event_id=event_id_warsaw,
                event_type="DEAT",
                year=1920,
                country="PL",
                city="Warsaw",
            ),
        ],
    )
    r = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "person_id",
        "suggestions",
        "undocumented_event_count",
        "sealed_scopes",  # Phase 15.11c (ADR-0082)
    }
    assert isinstance(body["undocumented_event_count"], int)
    assert isinstance(body["suggestions"], list)
    if body["suggestions"]:
        s = body["suggestions"][0]
        expected_keys = {
            "archive_id",
            "archive_name",
            "location_country",
            "location_city",
            "languages",
            "digitization_level",
            "priority_score",
            "reason",
            "related_event_id",
            "related_event_type",
        }
        assert set(s.keys()) == expected_keys
        assert isinstance(s["priority_score"], (int, float))
        assert 0.0 <= s["priority_score"] <= 1.0
        assert s["digitization_level"] in {"none", "partial", "full"}


@pytest.mark.asyncio
async def test_default_limit_is_ten(
    planner_client: AsyncClient,
    override_fetcher: FetcherOverride,
    make_event: EventBuilder,
    person_id: uuid.UUID,
    event_id_lodz: uuid.UUID,
) -> None:
    """Default limit = 10."""
    override_fetcher(
        [
            make_event(
                event_id=event_id_lodz,
                event_type="BIRT",
                year=1880,
                country="PL",
                city="Łódź",
            ),
        ],
    )
    r = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200
    assert len(r.json()["suggestions"]) <= 10


@pytest.mark.asyncio
async def test_limit_query_param_caps_results(
    planner_client: AsyncClient,
    override_fetcher: FetcherOverride,
    make_event: EventBuilder,
    person_id: uuid.UUID,
    event_id_lodz: uuid.UUID,
) -> None:
    """``?limit=3`` ограничивает результат до 3."""
    override_fetcher(
        [
            make_event(
                event_id=event_id_lodz,
                event_type="BIRT",
                year=1880,
                country="PL",
                city="Łódź",
            ),
        ],
    )
    r = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        params={"limit": 3},
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200
    assert len(r.json()["suggestions"]) == 3


@pytest.mark.asyncio
async def test_limit_query_param_validation(
    planner_client: AsyncClient,
    override_fetcher: FetcherOverride,
    person_id: uuid.UUID,
) -> None:
    """``?limit=999`` → 422 (Query has le=50)."""
    override_fetcher([])
    r = await planner_client.get(
        f"/archive-planner/persons/{person_id}/suggestions",
        params={"limit": 999},
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 422
