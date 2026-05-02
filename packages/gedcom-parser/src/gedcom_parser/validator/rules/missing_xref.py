"""Missing primary key — top-level INDI / FAM / SOUR record without an xref.

Records без xref'а silently dropped'аются ``GedcomDocument.from_records``
(там ``if record.xref_id is None: continue``), так что они не доходят до
document-level scan. Чтобы emit'ить finding, правило требует
``ctx.raw_records`` — плоский список корневых ``GedcomRecord``, в котором
скипнутые записи всё ещё видны.

Если ``ctx.raw_records`` пуст (например, CLI-вызов передал только
document) — правило тихо возвращает []. Это не "rule failure", это
"insufficient input"; downstream UI не должен интерпретировать
"no findings" как "no missing xrefs".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.validator.types import Finding, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.validator.types import ValidatorContext


# Top-level теги, у которых xref обязателен по GEDCOM 5.5.5. HEAD/TRLR
# исключаются — у них xref'а никогда не бывает.
_REQUIRES_XREF: frozenset[str] = frozenset({"INDI", "FAM", "SOUR", "NOTE", "OBJE", "REPO", "SUBM"})


class MissingXrefRule:
    """Top-level record of a known kind missing its xref → ERROR.

    Requires ``ctx.raw_records`` — без него silently no-op (см. модульный
    docstring).
    """

    rule_id: str = "missing_xref"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        if not ctx.raw_records:
            return
        for record in ctx.raw_records:
            if record.tag not in _REQUIRES_XREF:
                continue
            if record.xref_id is not None:
                continue
            yield Finding(
                rule_id=self.rule_id,
                severity=Severity.ERROR,
                message=(
                    f"Top-level {record.tag} record at line {record.line_no} "
                    f"is missing its xref id — record was dropped during import."
                ),
                suggested_fix=(
                    "Add a unique xref (e.g. @I123@) to the record header line, "
                    "or merge the record into another that already has one."
                ),
                context={"tag": record.tag, "line_no": record.line_no},
            )


__all__ = ["MissingXrefRule"]
