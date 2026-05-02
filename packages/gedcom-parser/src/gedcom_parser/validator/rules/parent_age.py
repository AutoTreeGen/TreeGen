"""Parent-age-at-child-birth rules (Phase 5.8).

Two rules — one per parent role — because (а) symmetric thresholds
differ (mothers cap at 55, fathers at 75), and (б) split makes UI
filtering straightforward (`rule_id="mother_age_high"` vs
`father_age_high`).

Thresholds (per spec):

- Mother: warn `<13`, error `>55`
- Father: warn `<14`, error `>75`

Skipped silently when either birth year is missing — год-precision
достаточен; дробление по месяцам бессмысленно для подобных гайдрейлов.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.validator._date_utils import birth_year, years_between
from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.entities import Family, Person
    from gedcom_parser.validator.types import ValidatorContext


_MOTHER_LOW = 13
_MOTHER_HIGH = 55
_FATHER_LOW = 14
_FATHER_HIGH = 75


def _check_parent_age(
    *,
    parent: Person | None,
    child: Person | None,
    family: Family,
    parent_role: str,
    low: int,
    high: int,
    rule_id_low: str,
    rule_id_high: str,
) -> Iterable[Finding]:
    """Общая логика для mother- и father-age правил."""
    if parent is None or child is None:
        return
    parent_birth_year = birth_year(parent)
    child_birth_year = birth_year(child)
    age = years_between(parent_birth_year, child_birth_year)
    if age is None:
        return
    if age < low:
        yield Finding(
            rule_id=rule_id_low,
            severity=Severity.WARNING,
            message=(
                f"{parent_role.capitalize()} {parent.xref_id} would have been "
                f"{age} years old at child {child.xref_id}'s birth "
                f"(threshold: {low})."
            ),
            person_xref=parent.xref_id,
            family_xref=family.xref_id,
            suggested_fix=(
                f"Verify {parent_role}'s birth date or remove this child from the family."
            ),
            context={
                "parent_birth_year": parent_birth_year,
                "child_birth_year": child_birth_year,
                "age_at_birth_years": age,
                "child_xref": child.xref_id,
                "parent_role": parent_role,
            },
        )
    elif age > high:
        yield Finding(
            rule_id=rule_id_high,
            severity=Severity.ERROR,
            message=(
                f"{parent_role.capitalize()} {parent.xref_id} would have been "
                f"{age} years old at child {child.xref_id}'s birth "
                f"(threshold: {high})."
            ),
            person_xref=parent.xref_id,
            family_xref=family.xref_id,
            suggested_fix=(
                f"Verify {parent_role}'s birth date — parent older than {high} "
                "is biologically implausible."
            ),
            context={
                "parent_birth_year": parent_birth_year,
                "child_birth_year": child_birth_year,
                "age_at_birth_years": age,
                "child_xref": child.xref_id,
                "parent_role": parent_role,
            },
        )


class MotherAgeAtChildBirthRule:
    """Mother age <13 → WARNING, >55 → ERROR."""

    rule_id: str = "mother_age_at_child_birth"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        for family in doc.families.values():
            if family.wife_xref is None:
                continue
            mother = doc.get_person(family.wife_xref)
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                yield from _check_parent_age(
                    parent=mother,
                    child=child,
                    family=family,
                    parent_role="mother",
                    low=_MOTHER_LOW,
                    high=_MOTHER_HIGH,
                    rule_id_low="mother_age_low",
                    rule_id_high="mother_age_high",
                )


class FatherAgeAtChildBirthRule:
    """Father age <14 → WARNING, >75 → ERROR."""

    rule_id: str = "father_age_at_child_birth"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        for family in doc.families.values():
            if family.husband_xref is None:
                continue
            father = doc.get_person(family.husband_xref)
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                yield from _check_parent_age(
                    parent=father,
                    child=child,
                    family=family,
                    parent_role="father",
                    low=_FATHER_LOW,
                    high=_FATHER_HIGH,
                    rule_id_low="father_age_low",
                    rule_id_high="father_age_high",
                )


__all__ = ["FatherAgeAtChildBirthRule", "MotherAgeAtChildBirthRule"]
