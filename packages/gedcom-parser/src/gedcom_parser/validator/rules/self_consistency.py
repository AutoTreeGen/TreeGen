"""Single-person logical consistency: death before birth.

Простейшее правило: ``death_year < birth_year`` — почти всегда
data-entry typo. Использует год-precision (lower-bound каждой даты).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.validator._date_utils import birth_year, death_year
from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.validator.types import ValidatorContext


class DeathBeforeBirthRule:
    """Death year strictly before birth year → ERROR (data entry typo)."""

    rule_id: str = "death_before_birth"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        for person in doc.persons.values():
            b = birth_year(person)
            d = death_year(person)
            if b is None or d is None:
                continue
            if d < b:
                yield Finding(
                    rule_id=self.rule_id,
                    severity=Severity.ERROR,
                    message=(f"Person {person.xref_id}: death year {d} is before birth year {b}."),
                    person_xref=person.xref_id,
                    suggested_fix="Swap birth/death dates or correct the typo.",
                    context={"birth_year": b, "death_year": d},
                )


__all__ = ["DeathBeforeBirthRule"]
