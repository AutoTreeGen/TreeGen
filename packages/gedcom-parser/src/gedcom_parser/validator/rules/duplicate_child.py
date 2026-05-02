"""Duplicate child — same person_xref listed twice as a child of one family."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.validator.types import ValidatorContext


class DuplicateChildRule:
    """Same person listed multiple times in family.children → ERROR per duplicate."""

    rule_id: str = "duplicate_child"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        for family in doc.families.values():
            if not family.children_xrefs:
                continue
            counts = Counter(family.children_xrefs)
            for child_xref, count in counts.items():
                if count <= 1:
                    continue
                yield Finding(
                    rule_id=self.rule_id,
                    severity=Severity.ERROR,
                    message=(
                        f"Family {family.xref_id}: child {child_xref} appears "
                        f"{count} times in CHIL records."
                    ),
                    family_xref=family.xref_id,
                    person_xref=child_xref,
                    suggested_fix="Remove the duplicate CHIL entries.",
                    context={"child_xref": child_xref, "occurrences": count},
                )


__all__ = ["DuplicateChildRule"]
