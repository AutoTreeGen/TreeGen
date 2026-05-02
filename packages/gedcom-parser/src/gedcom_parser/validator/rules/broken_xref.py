"""Broken FAMS / FAMC / HUSB / WIFE / CHIL etc. cross-references.

Wraps :meth:`GedcomDocument.verify_references`, which already implements
full cross-ref scanning. We just convert each :class:`BrokenRef` into a
:class:`Finding` with a stable rule_id-per-relationship-kind so downstream
UIs can filter (e.g. "show only broken FAMS, ignore broken NOTE refs").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.validator.types import ValidatorContext


# Map field-name (from BrokenRef.field) → rule_id for stable downstream filtering.
# Unmapped fields fall back to a generic rule_id with the field embedded in context.
_FIELD_TO_RULE_ID: dict[str, str] = {
    "FAMS": "broken_xref_fams",
    "FAMC": "broken_xref_famc",
    "HUSB": "broken_xref_husb",
    "WIFE": "broken_xref_wife",
    "CHIL": "broken_xref_chil",
    "NOTE": "broken_xref_note",
    "SOUR": "broken_xref_sour",
    "OBJE": "broken_xref_obje",
    "REPO": "broken_xref_repo",
    "SUBM": "broken_xref_subm",
}

# Severity by relationship kind: structural lineage (FAMS/FAMC/HUSB/WIFE/CHIL)
# is ERROR — без них импорт получает orphan'ов; вспомогательные ссылки
# (NOTE/SOUR/OBJE/REPO/SUBM) — WARNING (контент потерян, но дерево не сломано).
_STRUCTURAL_FIELDS: frozenset[str] = frozenset({"FAMS", "FAMC", "HUSB", "WIFE", "CHIL"})


class BrokenCrossRefRule:
    """Wrap GedcomDocument.verify_references() output as Findings."""

    rule_id: str = "broken_xref"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        # warn=False — verify_references иначе спамит warnings.warn(); валидатор
        # сам контролирует output через Finding'и.
        for broken in doc.verify_references(warn=False):
            # Field может быть составным (e.g. ``"BIRT.SOUR"`` — SOUR внутри
            # event'а). Берём последний сегмент для mapping'а.
            field_key = broken.field.rsplit(".", 1)[-1]
            rule_id = _FIELD_TO_RULE_ID.get(field_key, "broken_xref_other")
            severity = Severity.ERROR if field_key in _STRUCTURAL_FIELDS else Severity.WARNING
            person_xref = broken.owner_xref if broken.owner_kind == "person" else None
            family_xref = broken.owner_xref if broken.owner_kind == "family" else None
            yield Finding(
                rule_id=rule_id,
                severity=severity,
                message=(
                    f"Dangling {field_key} reference: {broken.owner_kind} "
                    f"{broken.owner_xref} → {broken.expected_kind} "
                    f"{broken.target_xref} (target not found)."
                ),
                person_xref=person_xref,
                family_xref=family_xref,
                suggested_fix=("Remove the dangling reference, or add the missing record."),
                context={
                    "owner_kind": broken.owner_kind,
                    "owner_xref": broken.owner_xref,
                    "field": broken.field,
                    "target_xref": broken.target_xref,
                    "expected_kind": broken.expected_kind,
                },
            )


__all__ = ["BrokenCrossRefRule"]
