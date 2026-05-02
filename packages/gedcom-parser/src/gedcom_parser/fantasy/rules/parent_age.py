"""Parent age at child birth rules (Phase 5.10).

* :class:`ParentTooYoungAtBirthRule` — parent.age < 9 — HIGH (likely
  data error; biologically possible but extremely rare and almost
  always reflects misattribution).
* :class:`ParentTooOldAtBirthRule` — mother.age > 55 OR father.age > 80
  — WARNING (rare but plausible — IVF, late paternity).

Validator's MotherAge/FatherAge rule (Phase 5.8) использует month-precision
для строгого спора; здесь — year-precision для broader fabrication
sweep. Намеренное overlap: validator катит на every import, fantasy
filter — opt-in scan с UI dismiss.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.fantasy.types import (
    FantasyContext,
    FantasyFlag,
    FantasySeverity,
)
from gedcom_parser.validator._date_utils import birth_year

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.entities import Family, Person


# Brief thresholds.
_MIN_PARENT_AGE = 9
_MAX_MOTHER_AGE = 55
_MAX_FATHER_AGE = 80


class ParentTooYoungAtBirthRule:
    """Parent under 9 years old at child birth — HIGH."""

    rule_id: str = "parent_too_young_at_birth"
    default_severity: FantasySeverity = FantasySeverity.HIGH

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        for family in doc.families.values():
            mother = doc.get_person(family.wife_xref) if family.wife_xref else None
            father = doc.get_person(family.husband_xref) if family.husband_xref else None
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                if child is None:
                    continue
                child_b = birth_year(child)
                if child_b is None:
                    continue
                yield from self._check(child, child_b, mother, family, role="mother")
                yield from self._check(child, child_b, father, family, role="father")

    def _check(
        self,
        child: Person,
        child_b: int,
        parent: Person | None,
        family: Family,
        *,
        role: str,
    ) -> Iterable[FantasyFlag]:
        if parent is None:
            return
        parent_b = birth_year(parent)
        if parent_b is None:
            return
        age = child_b - parent_b
        if 0 <= age < _MIN_PARENT_AGE:
            yield FantasyFlag(
                rule_id=self.rule_id,
                severity=FantasySeverity.HIGH,
                confidence=0.85,
                reason=(
                    f"{role.capitalize()} {parent.xref_id} was age {age} at child "
                    f"{child.xref_id}'s birth — biologically implausible "
                    f"(threshold {_MIN_PARENT_AGE} years)."
                ),
                person_xref=child.xref_id,
                family_xref=family.xref_id,
                evidence={
                    "child_xref": child.xref_id,
                    "parent_xref": parent.xref_id,
                    "role": role,
                    "age_at_birth": age,
                    "threshold_years": _MIN_PARENT_AGE,
                },
                suggested_action=(f"Verify {role}'s birth year — likely off by a generation."),
            )


class ParentTooOldAtBirthRule:
    """Mother >55 or father >80 at child birth — WARNING (rare but plausible)."""

    rule_id: str = "parent_too_old_at_birth"
    default_severity: FantasySeverity = FantasySeverity.WARNING

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        for family in doc.families.values():
            mother = doc.get_person(family.wife_xref) if family.wife_xref else None
            father = doc.get_person(family.husband_xref) if family.husband_xref else None
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                if child is None:
                    continue
                child_b = birth_year(child)
                if child_b is None:
                    continue
                yield from self._check_age(
                    child,
                    child_b,
                    mother,
                    family,
                    role="mother",
                    upper=_MAX_MOTHER_AGE,
                )
                yield from self._check_age(
                    child,
                    child_b,
                    father,
                    family,
                    role="father",
                    upper=_MAX_FATHER_AGE,
                )

    def _check_age(
        self,
        child: Person,
        child_b: int,
        parent: Person | None,
        family: Family,
        *,
        role: str,
        upper: int,
    ) -> Iterable[FantasyFlag]:
        if parent is None:
            return
        parent_b = birth_year(parent)
        if parent_b is None:
            return
        age = child_b - parent_b
        if age <= upper:
            return
        yield FantasyFlag(
            rule_id=self.rule_id,
            severity=FantasySeverity.WARNING,
            confidence=0.6,
            reason=(
                f"{role.capitalize()} {parent.xref_id} was age {age} at child "
                f"{child.xref_id}'s birth — above typical upper bound "
                f"({upper} for {role}). Plausible (IVF, late paternity) but worth verifying."
            ),
            person_xref=child.xref_id,
            family_xref=family.xref_id,
            evidence={
                "child_xref": child.xref_id,
                "parent_xref": parent.xref_id,
                "role": role,
                "age_at_birth": age,
                "upper_bound": upper,
            },
        )


__all__ = ["ParentTooOldAtBirthRule", "ParentTooYoungAtBirthRule"]
