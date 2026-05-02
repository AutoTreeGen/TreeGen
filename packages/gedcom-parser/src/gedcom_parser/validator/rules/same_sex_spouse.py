"""Same-sex spouse pair (warn — flag only, не блокировать).

Per spec: warning, not error. Реальные causes:

- Data-entry mistake (HUSB/WIFE поля перепутаны).
- Modern same-sex marriage (важная legitimate-кейс — поэтому warn,
  не error; user reviews and dismisses if intentional).
- GEDCOM 5.5.5 формально не поддерживает same-sex slots; это часто
  представлено через присвоение того же sex обоим slots либо через
  кастомные расширения.

Skipped silently когда хотя бы один пол ``U``/``X``/missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.validator.types import ValidatorContext


class SameSexSpousePairRule:
    """Both spouses share the same sex (M+M or F+F) → WARNING."""

    rule_id: str = "same_sex_spouse_pair"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        for family in doc.families.values():
            husband = doc.get_person(family.husband_xref) if family.husband_xref else None
            wife = doc.get_person(family.wife_xref) if family.wife_xref else None
            if husband is None or wife is None:
                continue
            if husband.sex not in ("M", "F") or wife.sex not in ("M", "F"):
                continue
            if husband.sex == wife.sex:
                yield Finding(
                    rule_id=self.rule_id,
                    severity=Severity.WARNING,
                    message=(
                        f"Family {family.xref_id} has same-sex spouses: "
                        f"HUSB {husband.xref_id} (sex={husband.sex}), "
                        f"WIFE {wife.xref_id} (sex={wife.sex})."
                    ),
                    family_xref=family.xref_id,
                    suggested_fix=(
                        "If accidental, swap HUSB/WIFE assignments or correct "
                        "the sex of one spouse. If intentional same-sex "
                        "marriage, this warning can be dismissed."
                    ),
                    context={
                        "husband_xref": husband.xref_id,
                        "wife_xref": wife.xref_id,
                        "shared_sex": husband.sex,
                    },
                )


__all__ = ["SameSexSpousePairRule"]
