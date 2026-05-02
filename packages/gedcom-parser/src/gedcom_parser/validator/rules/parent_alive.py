"""Child birth after parent death (month-precision).

Different from "father <14 / >75" because it isn't an age-gap heuristic —
it's strict logical impossibility (unless the father died **before** the
child was conceived, leaving a posthumous birth window of ~9 months).

Implementation:

- Use month-precision dates only. If either date is year-only, skip — too
  much ambiguity to call.
- Mother: child birth strictly after mother's death is impossible (mother
  must survive at least to delivery).
- Father: child birth more than ~10 months after father's death is
  implausible (posthumous birth grace = 10 months ≈ longest plausible
  gestation + buffer).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from gedcom_parser.validator._date_utils import (
    event_month_precision_lower,
    event_month_precision_upper,
    find_event,
)
from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.entities import Family, Person
    from gedcom_parser.validator.types import ValidatorContext


# Posthumous-birth window for fathers — ~10 months covers longest plausible
# gestation (averages 9 months but >40 weeks happens, plus a bit of buffer
# for date imprecision at month-level).
_POSTHUMOUS_FATHER_GRACE_DAYS = 305


class ChildBirthAfterParentDeathRule:
    """Child birth strictly after mother death OR >10 mo after father death."""

    rule_id: str = "child_birth_after_parent_death"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        for family in doc.families.values():
            mother = doc.get_person(family.wife_xref) if family.wife_xref else None
            father = doc.get_person(family.husband_xref) if family.husband_xref else None
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                if child is None:
                    continue
                yield from self._check_one_parent(child, mother, family, role="mother")
                yield from self._check_one_parent(child, father, family, role="father")

    def _check_one_parent(
        self,
        child: Person,
        parent: Person | None,
        family: Family,
        *,
        role: str,
    ) -> Iterable[Finding]:
        if parent is None:
            return
        # Earliest plausible child birth — нижняя граница month-precision.
        child_birth_lower = event_month_precision_lower(find_event(child, "BIRT"))
        # Latest plausible parent death — верхняя граница month-precision.
        parent_death_upper = event_month_precision_upper(find_event(parent, "DEAT"))
        if child_birth_lower is None or parent_death_upper is None:
            return

        if role == "mother":
            # Strict: birth strictly after mother's last possible death day.
            if child_birth_lower > parent_death_upper:
                yield Finding(
                    rule_id="child_born_after_mother_death",
                    severity=Severity.ERROR,
                    message=(
                        f"Child {child.xref_id} born "
                        f"{child_birth_lower.isoformat()} but mother "
                        f"{parent.xref_id} died by "
                        f"{parent_death_upper.isoformat()}."
                    ),
                    person_xref=child.xref_id,
                    family_xref=family.xref_id,
                    suggested_fix=(
                        "Verify both dates; either child belongs to a different "
                        "family or the mother's death date is wrong."
                    ),
                    context={
                        "child_birth": child_birth_lower.isoformat(),
                        "mother_death": parent_death_upper.isoformat(),
                        "mother_xref": parent.xref_id,
                    },
                )
        elif role == "father" and (
            # Posthumous birth grace: father may die up to ~10mo before birth.
            child_birth_lower > parent_death_upper + timedelta(days=_POSTHUMOUS_FATHER_GRACE_DAYS)
        ):
            yield Finding(
                rule_id="child_born_long_after_father_death",
                severity=Severity.ERROR,
                message=(
                    f"Child {child.xref_id} born "
                    f"{child_birth_lower.isoformat()} but father "
                    f"{parent.xref_id} died by "
                    f"{parent_death_upper.isoformat()} (posthumous-birth "
                    f"window of {_POSTHUMOUS_FATHER_GRACE_DAYS} days exceeded)."
                ),
                person_xref=child.xref_id,
                family_xref=family.xref_id,
                suggested_fix=("Verify both dates; child likely belongs to a different father."),
                context={
                    "child_birth": child_birth_lower.isoformat(),
                    "father_death": parent_death_upper.isoformat(),
                    "father_xref": parent.xref_id,
                    "posthumous_grace_days": _POSTHUMOUS_FATHER_GRACE_DAYS,
                },
            )


__all__ = ["ChildBirthAfterParentDeathRule"]
