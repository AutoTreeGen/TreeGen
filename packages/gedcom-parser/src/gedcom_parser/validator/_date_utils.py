"""Date arithmetic helpers for validator rules.

Все функции работают с :class:`gedcom_parser.dates.ParsedDate` и его
``date_lower`` / ``date_upper`` границами. Год-precision правила (age gaps)
используют ``date_lower.year`` (нижнюю границу диапазона); month-precision
правила (child birth after parent death) требуют, чтобы у обоих дат
``date_lower`` и ``date_upper`` ссылались на тот же месяц (то есть
точная или month-only дата, не годовой диапазон).

Дизайн-нота: все функции возвращают ``None`` при недостатке данных.
Caller-rule интерпретирует ``None`` как «не применимо, пропустить» —
это центральная идея advisory-validator'а: лучше промолчать, чем
emit'ить шумный finding на неполных данных.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date

    from gedcom_parser.entities import Event, Person


def find_event(person: Person | None, tag: str) -> Event | None:
    """Найти первое событие персоны с указанным tag (BIRT, DEAT, ...).

    Если у персоны несколько BIRT (что технически валидно в GEDCOM —
    например, двойной баптизм), берём первое — оно обычно primary.
    """
    if person is None:
        return None
    for event in person.events:
        if event.tag == tag:
            return event
    return None


def event_year(event: Event | None) -> int | None:
    """Год нижней границы события или ``None``.

    Используется age-gap правилами: для них достаточно года.
    """
    if event is None or event.date is None or event.date.date_lower is None:
        return None
    return event.date.date_lower.year


def birth_year(person: Person | None) -> int | None:
    """Год рождения персоны (lower-bound) или ``None``."""
    return event_year(find_event(person, "BIRT"))


def death_year(person: Person | None) -> int | None:
    """Год смерти персоны (lower-bound) или ``None``."""
    return event_year(find_event(person, "DEAT"))


def event_month_precision_lower(event: Event | None) -> date | None:
    """Нижняя граница даты события, ЕСЛИ дата имеет месяц-precision (или точнее).

    Возвращает ``None`` для year-only дат (когда ``date_lower=Jan 1`` и
    ``date_upper=Dec 31``) — таким способом мы фильтруем month-precision
    правила и не выдаём ложных findings.
    """
    if event is None or event.date is None:
        return None
    parsed = event.date
    lower = parsed.date_lower
    upper = parsed.date_upper
    if lower is None or upper is None:
        return None
    # Если lower и upper в разных месяцах → year-only либо range/period.
    if lower.year != upper.year or lower.month != upper.month:
        return None
    return lower


def event_month_precision_upper(event: Event | None) -> date | None:
    """Верхняя граница даты события, ЕСЛИ месяц-precision (или точнее).

    Симметрично :func:`event_month_precision_lower` — нужна для строгого
    неравенства "child birth STRICTLY after parent death".
    """
    if event is None or event.date is None:
        return None
    parsed = event.date
    lower = parsed.date_lower
    upper = parsed.date_upper
    if lower is None or upper is None:
        return None
    if lower.year != upper.year or lower.month != upper.month:
        return None
    return upper


def years_between(earlier_year: int | None, later_year: int | None) -> int | None:
    """Целочисленная разница ``later - earlier`` или ``None``.

    Может быть отрицательной (например, child_birth_year < parent_birth_year),
    что сам по себе сигнал данных-нонsens'а.
    """
    if earlier_year is None or later_year is None:
        return None
    return later_year - earlier_year


__all__ = [
    "birth_year",
    "death_year",
    "event_month_precision_lower",
    "event_month_precision_upper",
    "event_year",
    "find_event",
    "years_between",
]
