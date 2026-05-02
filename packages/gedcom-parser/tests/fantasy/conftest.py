"""Shared fixtures для fantasy filter tests.

Дублирует minimal-shape builders из ``tests/validator/conftest.py``
(не импортируется cross-folder из-за pytest --import-mode=importlib).
Исключительно year-precision (fantasy rules не используют month).
"""

from __future__ import annotations

from datetime import date

from gedcom_parser.dates import ParsedDate
from gedcom_parser.document import GedcomDocument
from gedcom_parser.entities import Event, Family, Person


def _year_event(tag: str, year: int) -> Event:
    return Event(
        tag=tag,
        date_raw=str(year),
        date=ParsedDate(
            raw=str(year),
            date_lower=date(year, 1, 1),
            date_upper=date(year, 12, 31),
        ),
    )


def make_person(
    xref: str,
    *,
    sex: str | None = None,
    birth_year: int | None = None,
    death_year: int | None = None,
    families_as_spouse: tuple[str, ...] = (),
    families_as_child: tuple[str, ...] = (),
) -> Person:
    """Year-precision Person с опциональными BIRT/DEAT events."""
    events: list[Event] = []
    if birth_year is not None:
        events.append(_year_event("BIRT", birth_year))
    if death_year is not None:
        events.append(_year_event("DEAT", death_year))
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
    return Family(
        xref_id=xref,
        husband_xref=husband_xref,
        wife_xref=wife_xref,
        children_xrefs=children_xrefs,
    )


def make_doc(
    persons: list[Person] | None = None,
    families: list[Family] | None = None,
) -> GedcomDocument:
    return GedcomDocument(
        persons={p.xref_id: p for p in (persons or [])},
        families={f.xref_id: f for f in (families or [])},
    )
