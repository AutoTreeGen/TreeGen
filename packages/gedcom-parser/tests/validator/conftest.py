"""Shared fixtures + builders for validator tests.

Каждое правило тестируется на минимально-достаточном synthetic
:class:`GedcomDocument`. Эти builders сокращают boilerplate, не вводя
DSL-overhead — каждый тест читается как «вот persons, вот family,
проверь N findings».
"""

from __future__ import annotations

from datetime import date

from gedcom_parser.dates import ParsedDate
from gedcom_parser.document import GedcomDocument
from gedcom_parser.entities import Event, Family, Person


def make_event(
    tag: str,
    *,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    is_year_only: bool = False,
) -> Event:
    """Construct a single Event with optional date precision.

    - ``year`` only → year-precision (date_lower=Jan 1, date_upper=Dec 31).
    - ``year + month`` → month-precision (date_lower=1st, date_upper=last day).
    - ``year + month + day`` → exact (lower == upper).
    - ``is_year_only=True`` forces year-only even if month/day given (used to
      test that month-precision rules skip year-only inputs).
    """
    if year is None:
        return Event(tag=tag)
    if is_year_only or (month is None and day is None):
        return Event(
            tag=tag,
            date_raw=str(year),
            date=ParsedDate(
                raw=str(year),
                date_lower=date(year, 1, 1),
                date_upper=date(year, 12, 31),
            ),
        )
    if day is None:
        # month-precision
        # последний день месяца — упрощённо: 28 (не имеет значения для тестов).
        return Event(
            tag=tag,
            date_raw=f"{month:02d} {year}",
            date=ParsedDate(
                raw=f"{month:02d} {year}",
                date_lower=date(year, month, 1),
                date_upper=date(year, month, 28),
            ),
        )
    # exact
    d = date(year, month, day)
    return Event(
        tag=tag,
        date_raw=d.isoformat(),
        date=ParsedDate(raw=d.isoformat(), date_lower=d, date_upper=d),
    )


def make_person(
    xref: str,
    *,
    sex: str | None = None,
    birth_year: int | None = None,
    birth_month: int | None = None,
    birth_day: int | None = None,
    death_year: int | None = None,
    death_month: int | None = None,
    death_day: int | None = None,
    families_as_spouse: tuple[str, ...] = (),
    families_as_child: tuple[str, ...] = (),
) -> Person:
    """Convenience constructor for a Person with optional birth/death events."""
    events: list[Event] = []
    if birth_year is not None:
        events.append(make_event("BIRT", year=birth_year, month=birth_month, day=birth_day))
    if death_year is not None:
        events.append(make_event("DEAT", year=death_year, month=death_month, day=death_day))
    return Person(
        xref_id=xref,
        sex=sex,
        events=tuple(events),
        families_as_spouse=families_as_spouse,
        families_as_child=families_as_child,
    )


def make_family(
    xref: str,
    *,
    husband_xref: str | None = None,
    wife_xref: str | None = None,
    children_xrefs: tuple[str, ...] = (),
) -> Family:
    """Convenience constructor for a Family."""
    return Family(
        xref_id=xref,
        husband_xref=husband_xref,
        wife_xref=wife_xref,
        children_xrefs=children_xrefs,
    )


def make_doc(persons: list[Person] = (), families: list[Family] = ()) -> GedcomDocument:
    """Construct a GedcomDocument from lists of persons + families."""
    return GedcomDocument(
        persons={p.xref_id: p for p in persons},
        families={f.xref_id: f for f in families},
    )
