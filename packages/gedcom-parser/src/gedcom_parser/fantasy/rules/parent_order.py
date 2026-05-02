"""Parent-vs-child birth ordering rule (Phase 5.10).

* :class:`ChildBeforeParentBirthRule` — child.birth_year < parent.birth_year.
  CRITICAL: child cannot be born before parent's birth.

Validator имеет смежное правило MotherAge/FatherAge (Phase 5.8) — там
focus на «слишком молодой родитель», здесь — на полную инверсию (parent
ещё не родился).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.fantasy.types import (
    MAX_CONFIDENCE,
    FantasyContext,
    FantasyFlag,
    FantasySeverity,
)
from gedcom_parser.validator._date_utils import birth_year

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.entities import Family, Person


class ChildBeforeParentBirthRule:
    """Child birth year < either parent's birth year — critical impossibility."""

    rule_id: str = "child_before_parent_birth"
    default_severity: FantasySeverity = FantasySeverity.CRITICAL

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
                child_birth = birth_year(child)
                if child_birth is None:
                    continue
                yield from self._check_one(child, child_birth, mother, family, role="mother")
                yield from self._check_one(child, child_birth, father, family, role="father")

    def _check_one(
        self,
        child: Person,
        child_birth: int,
        parent: Person | None,
        family: Family,
        *,
        role: str,
    ) -> Iterable[FantasyFlag]:
        if parent is None:
            return
        parent_birth = birth_year(parent)
        if parent_birth is None or child_birth >= parent_birth:
            return
        yield FantasyFlag(
            rule_id=self.rule_id,
            severity=FantasySeverity.CRITICAL,
            confidence=MAX_CONFIDENCE,
            reason=(
                f"Child {child.xref_id} (birth {child_birth}) is older than "
                f"{role} {parent.xref_id} (birth {parent_birth}) — "
                "child cannot be born before parent's birth."
            ),
            person_xref=child.xref_id,
            family_xref=family.xref_id,
            evidence={
                "child_birth_year": child_birth,
                "parent_birth_year": parent_birth,
                "parent_xref": parent.xref_id,
                "role": role,
            },
            suggested_action=(
                f"Verify {role}'s birth year or check if this child belongs to a "
                "different family (likely cross-link error)."
            ),
        )


__all__ = ["ChildBeforeParentBirthRule"]
