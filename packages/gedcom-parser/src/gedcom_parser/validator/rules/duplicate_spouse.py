"""Duplicate spouse — same person in both husband AND wife slots of one family.

Interpretation chosen for v1: same ``person_xref`` appears in both ``HUSB``
and ``WIFE`` slots of the same family record (typically a data-entry
error — родитель указан как партнёр самому себе). Other interpretations
("same person as spouse to one root via two different families") are
defer'ed — those require root-context which validator не имеет.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.validator.types import ValidatorContext


class DuplicateSpouseRule:
    """Husband and wife xrefs are equal → ERROR."""

    rule_id: str = "duplicate_spouse"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        for family in doc.families.values():
            if family.husband_xref is None or family.wife_xref is None:
                continue
            if family.husband_xref == family.wife_xref:
                yield Finding(
                    rule_id=self.rule_id,
                    severity=Severity.ERROR,
                    message=(
                        f"Family {family.xref_id}: same person {family.husband_xref} "
                        f"appears as both HUSB and WIFE."
                    ),
                    family_xref=family.xref_id,
                    person_xref=family.husband_xref,
                    suggested_fix=("Remove the duplicate slot or correct one of the assignments."),
                    context={"duplicate_xref": family.husband_xref},
                )


__all__ = ["DuplicateSpouseRule"]
