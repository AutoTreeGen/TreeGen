"""Per-person date-impossibility rules (Phase 5.10).

* :class:`BirthAfterDeathRule` — birth_year > death_year. CRITICAL.
* :class:`ImpossibleLifespanRule` — span > 122 (Calment limit). HIGH/CRITICAL
  по марже превышения.

Используем year-precision (``birth_year`` / ``death_year`` из
validator's ``_date_utils``) — это match'ится с brief'ом и достаточно для
fabrication detection. Validator уже имеет month-precision rules для
точного спора (Phase 5.8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.fantasy.types import (
    MAX_CONFIDENCE,
    FantasyContext,
    FantasyFlag,
    FantasySeverity,
)
from gedcom_parser.validator._date_utils import birth_year, death_year

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument


# Jeanne Calment, 1875–1997, 122 years 164 days — verified record.
_CALMENT_LIMIT_YEARS = 122
# Эскалация HIGH→CRITICAL: precedent above 130 не подтверждён ни одной
# серьёзной верификационной комиссией.
_CRITICAL_LIFESPAN_YEARS = 130


class BirthAfterDeathRule:
    """person.birth_year > person.death_year — критическая impossibility."""

    rule_id: str = "birth_after_death"
    default_severity: FantasySeverity = FantasySeverity.CRITICAL

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        for person in doc.persons.values():
            b = birth_year(person)
            d = death_year(person)
            if b is None or d is None:
                continue
            if b > d:
                yield FantasyFlag(
                    rule_id=self.rule_id,
                    severity=FantasySeverity.CRITICAL,
                    confidence=MAX_CONFIDENCE,
                    reason=(
                        f"Person {person.xref_id} has birth year {b} after death year {d} "
                        "— logically impossible. Likely a data-entry typo or fabrication."
                    ),
                    person_xref=person.xref_id,
                    evidence={"birth_year": b, "death_year": d},
                    suggested_action="Verify both dates against original records.",
                )


class ImpossibleLifespanRule:
    """death_year - birth_year > 122 (Calment limit). HIGH или CRITICAL."""

    rule_id: str = "impossible_lifespan"
    default_severity: FantasySeverity = FantasySeverity.HIGH

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        for person in doc.persons.values():
            b = birth_year(person)
            d = death_year(person)
            if b is None or d is None:
                continue
            span = d - b
            if span <= _CALMENT_LIMIT_YEARS:
                continue
            # Эскалация по марже превышения.
            if span > _CRITICAL_LIFESPAN_YEARS:
                severity = FantasySeverity.CRITICAL
                confidence = MAX_CONFIDENCE
            else:
                severity = FantasySeverity.HIGH
                confidence = 0.85
            yield FantasyFlag(
                rule_id=self.rule_id,
                severity=severity,
                confidence=confidence,
                reason=(
                    f"Person {person.xref_id} has lifespan of {span} years "
                    f"(birth {b}, death {d}). Verified human longevity record is "
                    f"{_CALMENT_LIMIT_YEARS} years (Jeanne Calment, 1875–1997)."
                ),
                person_xref=person.xref_id,
                evidence={
                    "birth_year": b,
                    "death_year": d,
                    "span_years": span,
                    "calment_limit": _CALMENT_LIMIT_YEARS,
                },
                suggested_action=(
                    "Verify birth and death dates against primary sources "
                    "(civil registry, gravestone, obituary)."
                ),
            )


__all__ = ["BirthAfterDeathRule", "ImpossibleLifespanRule"]
