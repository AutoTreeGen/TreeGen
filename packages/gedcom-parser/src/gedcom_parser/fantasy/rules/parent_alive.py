"""Parent-alive-at-conception rules (Phase 5.10).

* :class:`DeathBeforeChildBirthMotherRule` — mother.death_year <
  child.birth_year. CRITICAL (mother must survive at least to delivery).
* :class:`DeathBeforeChildBirthFatherRule` — father.death_year + 1 <
  child.birth_year (allows ~9-month posthumous-birth buffer). CRITICAL.

Validator's ChildBirthAfterParentDeathRule (Phase 5.8) уже покрывает
month-precision версию. Здесь — year-precision для broader sweep:
fantasy filter ловит trees с loose dates (year-only), которые validator
skip'ает.
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
    from gedcom_parser.entities import Family, Person


# Father grace: 1 год = ~9 mo gestation + buffer for year-precision noise.
_FATHER_POSTHUMOUS_GRACE_YEARS = 1


class DeathBeforeChildBirthMotherRule:
    """Mother died before child birth — critical (cannot have given birth).

    Year-precision strict: ``child_birth_year > mother_death_year``.
    """

    rule_id: str = "death_before_child_birth_mother"
    default_severity: FantasySeverity = FantasySeverity.CRITICAL

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        for family in doc.families.values():
            mother = doc.get_person(family.wife_xref) if family.wife_xref else None
            if mother is None:
                continue
            mother_d = death_year(mother)
            if mother_d is None:
                continue
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                if child is None:
                    continue
                child_b = birth_year(child)
                if child_b is None or child_b <= mother_d:
                    continue
                yield FantasyFlag(
                    rule_id=self.rule_id,
                    severity=FantasySeverity.CRITICAL,
                    confidence=MAX_CONFIDENCE,
                    reason=(
                        f"Child {child.xref_id} born {child_b} but mother "
                        f"{mother.xref_id} died {mother_d} — mother must survive "
                        "at least to delivery."
                    ),
                    person_xref=child.xref_id,
                    family_xref=family.xref_id,
                    evidence={
                        "child_birth_year": child_b,
                        "mother_death_year": mother_d,
                        "mother_xref": mother.xref_id,
                    },
                    suggested_action=(
                        "Verify mother's death date or check if this child belongs "
                        "to a different family."
                    ),
                )


class DeathBeforeChildBirthFatherRule:
    """Father died ≥1 year before child birth — critical (beyond posthumous gestation)."""

    rule_id: str = "death_before_child_birth_father"
    default_severity: FantasySeverity = FantasySeverity.CRITICAL

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        for family in doc.families.values():
            father = doc.get_person(family.husband_xref) if family.husband_xref else None
            if father is None:
                continue
            father_d = death_year(father)
            if father_d is None:
                continue
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                if child is None:
                    continue
                child_b = birth_year(child)
                if child_b is None:
                    continue
                # Allow: child_b ≤ father_d + 1 (posthumous birth in same year +1).
                if child_b <= father_d + _FATHER_POSTHUMOUS_GRACE_YEARS:
                    continue
                yield self._make_flag(child, child_b, father, father_d, family)

    def _make_flag(
        self,
        child: Person,
        child_b: int,
        father: Person,
        father_d: int,
        family: Family,
    ) -> FantasyFlag:
        return FantasyFlag(
            rule_id=self.rule_id,
            severity=FantasySeverity.CRITICAL,
            confidence=MAX_CONFIDENCE,
            reason=(
                f"Child {child.xref_id} born {child_b} but father "
                f"{father.xref_id} died {father_d} — gap of {child_b - father_d} "
                "years exceeds posthumous-birth window (~1 year)."
            ),
            person_xref=child.xref_id,
            family_xref=family.xref_id,
            evidence={
                "child_birth_year": child_b,
                "father_death_year": father_d,
                "father_xref": father.xref_id,
                "gap_years": child_b - father_d,
                "grace_years": _FATHER_POSTHUMOUS_GRACE_YEARS,
            },
            suggested_action=(
                "Verify father's death date or check if this child belongs to a "
                "different father (likely cross-link error)."
            ),
        )


__all__ = ["DeathBeforeChildBirthFatherRule", "DeathBeforeChildBirthMotherRule"]
