"""Pure-logic тесты для scorer'а (без БД, без HTTP)."""

from __future__ import annotations

import datetime as dt
import uuid

from archive_service.planner.catalog import CatalogArchive, get_catalog, load_catalog
from archive_service.planner.dto import UndocumentedEvent
from archive_service.planner.scorer import score_archives

EVENT_ID_1 = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
EVENT_ID_2 = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _event(
    *,
    eid: uuid.UUID = EVENT_ID_1,
    etype: str = "BIRT",
    year: int | None = 1880,
    country: str | None = "PL",
    city: str | None = "Łódź",
) -> UndocumentedEvent:
    date = dt.date(year, 1, 1) if year is not None else None
    return UndocumentedEvent(
        event_id=eid,
        event_type=etype,
        date_start=date,
        date_end=date,
        place_country_iso=country,
        place_city=city,
    )


def test_lodz_birth_picks_lodz_vital_records_first() -> None:
    """Birth in Łódź 1880, no source → pl-aplodz-vital — top-1."""
    catalog = get_catalog()
    suggestions, count = score_archives([_event()], catalog, locale="en", limit=10)
    assert count == 1
    assert len(suggestions) > 0
    top = suggestions[0]
    assert top.archive_id == "pl-aplodz-vital"
    assert top.related_event_id == EVENT_ID_1
    assert top.related_event_type == "BIRT"


def test_no_events_returns_empty_suggestions() -> None:
    """Все события задокументированы (передан пустой список) → suggestions=[]."""
    catalog = get_catalog()
    suggestions, count = score_archives([], catalog, locale="en")
    assert count == 0
    assert suggestions == []


def test_locale_ru_boosts_russian_language_archives() -> None:
    """Locale=ru даёт +0.05 архивам, у которых 'ru' в languages.

    Сравниваем pl-aplodz-vital (ru ∈ languages) vs pl-apkrakow (без ru):
    оба покрывают PL и 1880, но Łódź ещё совпадает по городу.
    Проверка: с locale=ru разница в score между ними ≥ 0.05 больше,
    чем с locale=en.
    """
    catalog = get_catalog()
    event_pl = _event(country="PL", city="Łódź", year=1880)

    sug_en, _ = score_archives([event_pl], catalog, locale="en", limit=25)
    sug_ru, _ = score_archives([event_pl], catalog, locale="ru", limit=25)

    by_id_en = {s.archive_id: s.priority_score for s in sug_en}
    by_id_ru = {s.archive_id: s.priority_score for s in sug_ru}

    # Архив с 'ru' в языках — выше в ru, чем в en.
    assert by_id_ru["pl-aplodz-vital"] > by_id_en["pl-aplodz-vital"]
    # Архив без 'ru' (apkrakow: pl,de,la) — НЕ должен сдвинуться вверх.
    assert by_id_ru["pl-apkrakow"] == by_id_en["pl-apkrakow"]


def test_no_country_match_excludes_archive() -> None:
    """Событие в PL не должно матчиться с архивами US (coverage=0)."""
    catalog = get_catalog()
    event_pl = _event(country="PL", city="Łódź")
    suggestions, _ = score_archives([event_pl], catalog, locale="en", limit=25)
    archive_ids = {s.archive_id for s in suggestions}
    # FHL покрывает много языков, но country=US — coverage=0 → excluded.
    assert "us-fhl-saltlake" not in archive_ids


def test_time_range_outside_archive_excludes() -> None:
    """Событие вне ``time_range_*`` архива — excluded."""
    # RGADA Moscow покрывает 1100-1800; событие 1900 → time_overlap=0.
    event_modern = _event(country="RU", city="Moscow", year=1900)
    catalog = get_catalog()
    suggestions, _ = score_archives([event_modern], catalog, limit=25)
    archive_ids = {s.archive_id for s in suggestions}
    assert "ru-rgada-moscow" not in archive_ids
    # GARF (1800-2000) покрывает — должен быть в результате.
    assert "ru-garf-moscow" in archive_ids


def test_dedup_by_archive_id() -> None:
    """Тот же архив не повторяется, если подходит к нескольким событиям."""
    catalog = get_catalog()
    e1 = _event(eid=EVENT_ID_1, year=1880, etype="BIRT")
    e2 = _event(eid=EVENT_ID_2, year=1900, etype="DEAT")
    suggestions, count = score_archives([e1, e2], catalog, limit=25)
    assert count == 2
    archive_ids = [s.archive_id for s in suggestions]
    assert len(archive_ids) == len(set(archive_ids)), "archive_id должен быть уникален"


def test_priority_score_in_zero_one_range() -> None:
    """``priority_score`` ∈ [0, 1] для всех suggestions (Pydantic ge=0/le=1)."""
    catalog = get_catalog()
    suggestions, _ = score_archives([_event()], catalog, limit=25)
    for s in suggestions:
        assert 0.0 <= s.priority_score <= 1.0


def test_limit_caps_results() -> None:
    """``limit`` ограничивает размер результата."""
    catalog = get_catalog()
    e_pl = _event(country="PL", city="Warsaw", year=1900, eid=EVENT_ID_1)
    suggestions, _ = score_archives([e_pl], catalog, limit=2)
    assert len(suggestions) == 2


def test_event_without_date_still_matches() -> None:
    """Событие без даты не отвергает archive'и (нейтральный time_overlap=0.5)."""
    e_undated = _event(year=None, country="PL", city="Łódź")
    catalog = get_catalog()
    suggestions, _ = score_archives([e_undated], catalog, limit=10)
    assert len(suggestions) > 0
    assert any(s.archive_id == "pl-aplodz-vital" for s in suggestions)


def test_sorted_descending_by_priority() -> None:
    """Suggestions отсортированы по priority_score desc."""
    catalog = get_catalog()
    suggestions, _ = score_archives([_event()], catalog, limit=25)
    scores = [s.priority_score for s in suggestions]
    assert scores == sorted(scores, reverse=True)


def test_reason_string_non_empty() -> None:
    """Reason — non-empty строка для каждой suggestion."""
    catalog: tuple[CatalogArchive, ...] = load_catalog()
    suggestions, _ = score_archives([_event()], catalog, limit=10)
    for s in suggestions:
        assert s.reason
