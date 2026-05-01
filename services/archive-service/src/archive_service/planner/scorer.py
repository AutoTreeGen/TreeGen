"""Скоринг архивов под недокументированное событие — pure logic.

Тестируется напрямую без БД: вход — список ``UndocumentedEvent`` +
catalog. Выход — список ``ArchiveSuggestion``, отсортированных по
``priority_score`` desc.

Формула:
    priority = 0.4 * coverage + 0.3 * time_overlap
             + 0.2 * digitization + 0.1 * locale_match

где:

* ``coverage`` — 1.0 если страна+город совпадают; 0.5 если только страна;
  0.0 если страна не совпадает (архив исключается).
* ``time_overlap`` — доля диапазона события, попадающая в покрытие архива
  (0..1). 0 → исключается.
* ``digitization`` — full=1.0, partial=0.5, none=0.2.
* ``locale_match`` — 1.0 если язык user.locale в catalog.languages, иначе 0.5.

Если у события нет даты — time_overlap считается = 0.5 (нейтрально), чтобы
не выкидывать релевантные по локации архивы.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from archive_service.planner.catalog import CatalogArchive, DigitizationLevel
from archive_service.planner.dto import UndocumentedEvent
from archive_service.planner.schemas import ArchiveSuggestion

_DIGITIZATION_WEIGHT: Final[dict[DigitizationLevel, float]] = {
    "full": 1.0,
    "partial": 0.5,
    "none": 0.2,
}

_W_COVERAGE: Final[float] = 0.4
_W_TIME: Final[float] = 0.3
_W_DIGITIZATION: Final[float] = 0.2
_W_LOCALE: Final[float] = 0.1


@dataclass(frozen=True)
class _Match:
    """Внутренний результат скоринга одного (event, archive) — до dedup."""

    archive: CatalogArchive
    event: UndocumentedEvent
    coverage: float
    time_overlap: float
    digitization: float
    locale: float
    priority: float


def _city_match(event_city: str | None, archive_city: str) -> bool:
    """Грубое сравнение городов: case-insensitive equality.

    Place.canonical_name может быть полной строкой ("Łódź, Polska"), поэтому
    проверяем substring в обе стороны после lower-case.
    """
    if not event_city:
        return False
    e = event_city.casefold()
    a = archive_city.casefold()
    return a in e or e in a


def _coverage_score(event: UndocumentedEvent, archive: CatalogArchive) -> float:
    if event.place_country_iso is None:
        return 0.0
    if event.place_country_iso.upper() != archive.location_country.upper():
        return 0.0
    if _city_match(event.place_city, archive.location_city):
        return 1.0
    return 0.5


def _time_overlap_score(event: UndocumentedEvent, archive: CatalogArchive) -> float:
    """Доля event-диапазона, попадающая в [archive.start, archive.end].

    Если у события нет дат — возвращаем 0.5 (нейтрально, не исключаем).
    """
    if event.date_start is None and event.date_end is None:
        return 0.5
    e_start = event.date_start.year if event.date_start else event.date_end.year  # type: ignore[union-attr]
    e_end = event.date_end.year if event.date_end else event.date_start.year  # type: ignore[union-attr]
    a_start = archive.time_range_start
    a_end = archive.time_range_end
    overlap_start = max(e_start, a_start)
    overlap_end = min(e_end, a_end)
    if overlap_end < overlap_start:
        return 0.0
    event_span = max(e_end - e_start, 1)  # +1 чтобы single-year события считались как 1
    overlap_span = overlap_end - overlap_start + 1
    return min(1.0, overlap_span / event_span)


def _locale_score(locale: str, archive: CatalogArchive) -> float:
    """Совпадение языка пользователя с языками архива.

    locale ожидается как ISO-639-1 (``'ru'``, ``'en'``, ``'pl-PL'`` → 'pl').
    """
    if not locale:
        return 0.5
    lang = locale.split("-", 1)[0].lower()
    return 1.0 if lang in archive.languages else 0.5


def _build_reason(match: _Match) -> str:
    """Короткое объяснение для UI."""
    parts: list[str] = []
    if match.coverage == 1.0:
        parts.append(
            f"covers {match.archive.location_city} ({match.archive.location_country})",
        )
    elif match.coverage > 0:
        parts.append(f"covers country {match.archive.location_country}")
    if match.time_overlap >= 0.9:
        parts.append("full time-range match")
    elif match.time_overlap > 0:
        parts.append(
            f"time {match.archive.time_range_start}–{match.archive.time_range_end} overlaps event",
        )
    if match.digitization >= 1.0:
        parts.append("fully digitized")
    elif match.digitization >= 0.5:
        parts.append("partially digitized")
    if match.locale >= 1.0:
        parts.append("language match for user locale")
    return "; ".join(parts) or "general candidate"


def score_archives(
    events: list[UndocumentedEvent],
    catalog: tuple[CatalogArchive, ...],
    locale: str = "en",
    limit: int = 10,
) -> tuple[list[ArchiveSuggestion], int]:
    """Скоринг + dedup + сортировка.

    Returns:
        ``(suggestions, undocumented_event_count)`` — где ``len(events)``
        возвращается отдельно потому, что не все события дают суггестию
        (если ни один архив их не покрыл).
    """
    matches: list[_Match] = []
    for event in events:
        for archive in catalog:
            coverage = _coverage_score(event, archive)
            if coverage <= 0:
                continue
            time_overlap = _time_overlap_score(event, archive)
            if time_overlap <= 0:
                continue
            digitization = _DIGITIZATION_WEIGHT[archive.digitization_level]
            locale_match = _locale_score(locale, archive)
            priority = (
                _W_COVERAGE * coverage
                + _W_TIME * time_overlap
                + _W_DIGITIZATION * digitization
                + _W_LOCALE * locale_match
            )
            matches.append(
                _Match(
                    archive=archive,
                    event=event,
                    coverage=coverage,
                    time_overlap=time_overlap,
                    digitization=digitization,
                    locale=locale_match,
                    priority=priority,
                ),
            )

    # Dedup: если один архив подходит к нескольким событиям, оставляем
    # лучший (event, archive) match. Иначе UX забивается дубликатами.
    best_per_archive: dict[str, _Match] = {}
    for m in matches:
        existing = best_per_archive.get(m.archive.archive_id)
        if existing is None or m.priority > existing.priority:
            best_per_archive[m.archive.archive_id] = m

    sorted_matches = sorted(
        best_per_archive.values(),
        key=lambda m: m.priority,
        reverse=True,
    )[:limit]

    suggestions = [
        ArchiveSuggestion(
            archive_id=m.archive.archive_id,
            archive_name=m.archive.name,
            location_country=m.archive.location_country,
            location_city=m.archive.location_city,
            languages=list(m.archive.languages),
            digitization_level=m.archive.digitization_level,
            priority_score=round(m.priority, 4),
            reason=_build_reason(m),
            related_event_id=m.event.event_id,
            related_event_type=m.event.event_type,
        )
        for m in sorted_matches
    ]
    return suggestions, len(events)
