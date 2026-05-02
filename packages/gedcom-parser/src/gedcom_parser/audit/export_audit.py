"""Export Audit: pre-export loss preview поверх Compatibility Simulator.

Главный API:

    audit_export(doc, target) -> ExportAudit

Алгоритм — дешёвая reshape-обёртка над :func:`gedcom_parser.compatibility.simulate`:

1. Прогнать ``simulate(doc, target)`` (Phase 5.6) — получить
   :class:`CompatibilityReport` с tag_drops / encoding_warnings /
   structure_changes.
2. Преобразовать каждую группу в :class:`AuditFinding`:

   * ``TagDrop``           → severity = ``lost``        (тег полностью
     не приедет на target).
   * ``EncodingIssue``     → severity = ``transformed`` (значение
     приедет с подстановкой/заменой `?`).
   * ``StructureChange``   → severity = ``warning``     (общий
     структурный риск, не привязан к конкретной записи).

3. Привязать findings к ``person_id``/``family_id``/``source_id`` по
   первой букве xref'а (``I`` / ``F`` / ``S``); ``HEAD``, ``_CUSTOM``
   и unknown-prefix остаются doc-level (все три id = ``None``).

4. Посчитать summary как ``{severity: count}``.

Поддерживаемые таргеты — те, для которых 5.6 уже поставил YAML-правила:
``ancestry``, ``myheritage``, ``familysearch``, ``gramps``. Брифовые
``rootsmagic`` / ``wikitree`` отложены до follow-up — анти-дрифт
запрещает дублировать rule-файлы, и расширение rule-набора — отдельная
работа уровня Phase 5.6.

Audit read-only: никогда не мутирует ``doc``, никогда не пишет в БД.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from gedcom_parser.compatibility import (
    EncodingIssue,
    StructureChange,
    TagDrop,
    simulate,
)
from gedcom_parser.compatibility.simulator import Target
from gedcom_parser.document import GedcomDocument


class TargetPlatform(StrEnum):
    """Поддерживаемые целевые платформы для export-audit (v1).

    Множество синхронизировано с YAML-правилами Phase 5.6
    (``compatibility/rules/``). Дополнительные платформы (rootsmagic,
    wikitree) появятся вместе со своими rule-файлами в отдельной фазе.
    """

    ancestry = "ancestry"
    myheritage = "myheritage"
    familysearch = "familysearch"
    gramps = "gramps"


class AuditSeverity(StrEnum):
    """Степень потери для одного finding'а."""

    lost = "lost"
    transformed = "transformed"
    warning = "warning"


_DOC_LEVEL_XREFS: Final[frozenset[str]] = frozenset({"HEAD", "_CUSTOM"})
_PERSON_PREFIX: Final[str] = "I"
_FAMILY_PREFIX: Final[str] = "F"
_SOURCE_PREFIX: Final[str] = "S"


class AuditFinding(BaseModel):
    """Один пункт в отчёте: что именно теряется/мутирует/предупреждает."""

    severity: AuditSeverity = Field(description="lost / transformed / warning")
    tag_path: str = Field(
        description=(
            "Путь, к которому привязан finding. Для tag-drops — quad ``INDI._UID``; "
            "для feature-drops — ``feature:<name>``; для encoding — ``NAME[0].GIVN`` "
            "и т.п.; для structure — ``document``."
        )
    )
    person_id: str | None = None
    family_id: str | None = None
    source_id: str | None = None
    rule_id: str = Field(
        description=(
            "Стабильный id правила, удобный для группировки в UI. "
            "Формат: ``<target>:<kind>:<discriminator>``."
        )
    )
    message: str = Field(min_length=1)
    suggested_action: str | None = Field(
        default=None,
        description="Подсказка пользователю: «что сделать, чтобы избежать потери».",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExportAudit(BaseModel):
    """Полный результат audit'а для одной пары (doc, target)."""

    target_platform: TargetPlatform
    total_records: int = Field(
        ge=0,
        description="persons + families + sources + notes + objects + repos + submitters.",
    )
    findings: tuple[AuditFinding, ...] = ()
    summary: dict[str, int] = Field(
        description="Счётчик findings по severity. Ключи — значения AuditSeverity.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


def audit_export(doc: GedcomDocument, target: TargetPlatform) -> ExportAudit:
    """Прогнать ``doc`` через 5.6-симулятор и собрать ExportAudit.

    Args:
        doc: Распарсенный GEDCOM-документ.
        target: Целевая платформа (must be in :class:`TargetPlatform`).

    Returns:
        :class:`ExportAudit` с findings и summary. Не мутирует ``doc``.
    """
    report = simulate(doc, _as_simulator_target(target))
    findings: list[AuditFinding] = []
    for drop in report.tag_drops:
        findings.append(_finding_from_tag_drop(drop, target))
    for issue in report.encoding_warnings:
        findings.append(_finding_from_encoding(issue, target))
    for change in report.structure_changes:
        findings.append(_finding_from_structure(change, target))
    total_records = (
        len(doc.persons)
        + len(doc.families)
        + len(doc.sources)
        + len(doc.notes)
        + len(doc.objects)
        + len(doc.repositories)
        + len(doc.submitters)
    )
    return ExportAudit(
        target_platform=target,
        total_records=total_records,
        findings=tuple(findings),
        summary=_summarize(findings),
    )


def _as_simulator_target(target: TargetPlatform) -> Target:
    """Cast TargetPlatform → 5.6 Target Literal.

    StrEnum value is а str; Target — Literal["ancestry", ...]. По значениям
    enum'а они тождественны, mypy в текущей версии видит StrEnum.value как
    Literal-совместимое.
    """
    return target.value


def _record_ids_for_xref(xref: str) -> tuple[str | None, str | None, str | None]:
    """Распределить xref по слотам (person_id, family_id, source_id).

    GEDCOM-конвенция: ``@I…@`` — индивид, ``@F…@`` — семья, ``@S…@`` —
    источник. Парсер сохраняет xref без амперсандов в ``Person.xref_id``
    и т.п. Для doc-level записей (``HEAD``) и synthetic-bucket'а
    (``_CUSTOM``) все три слота — ``None``.
    """
    if xref in _DOC_LEVEL_XREFS:
        return None, None, None
    if xref.startswith(_PERSON_PREFIX):
        return xref, None, None
    if xref.startswith(_FAMILY_PREFIX):
        return None, xref, None
    if xref.startswith(_SOURCE_PREFIX):
        return None, None, xref
    return None, None, None


def _finding_from_tag_drop(drop: TagDrop, target: TargetPlatform) -> AuditFinding:
    """TagDrop → AuditFinding(severity=lost). rule_id стабилен per-(target, tag_path)."""
    person_id, family_id, source_id = _record_ids_for_xref(drop.xref)
    return AuditFinding(
        severity=AuditSeverity.lost,
        tag_path=drop.tag_path,
        person_id=person_id,
        family_id=family_id,
        source_id=source_id,
        rule_id=f"{target.value}:drop:{drop.tag_path}",
        message=drop.reason,
        suggested_action=_suggest_for_tag_drop(drop.tag_path),
    )


def _finding_from_encoding(issue: EncodingIssue, target: TargetPlatform) -> AuditFinding:
    """EncodingIssue → AuditFinding(severity=transformed). message — diff пары."""
    person_id, family_id, source_id = _record_ids_for_xref(issue.xref)
    return AuditFinding(
        severity=AuditSeverity.transformed,
        tag_path=issue.field,
        person_id=person_id,
        family_id=family_id,
        source_id=source_id,
        rule_id=f"{target.value}:encoding:{issue.field}",
        message=f"{issue.original!r} -> {issue.will_become!r}",
        suggested_action="Pre-normalize text or accept transliteration before export.",
    )


def _finding_from_structure(change: StructureChange, target: TargetPlatform) -> AuditFinding:
    """StructureChange → AuditFinding(severity=warning). Не привязан к записи."""
    return AuditFinding(
        severity=AuditSeverity.warning,
        tag_path="document",
        rule_id=f"{target.value}:structure:{_short_label(change.description)}",
        message=change.description,
        suggested_action=None,
    )


def _suggest_for_tag_drop(tag_path: str) -> str | None:
    """Подсказка пользователю по типу drop'а. Возвращает ``None`` для общих случаев.

    Привязки — к стабильным feature-меткам Phase 5.6
    (см. ``simulator._yield_feature_hits``). Для proprietary-tag drops
    обычно подсказывать нечего: тег уже custom, на target всё равно
    исчезнет — никаким нормализованным переездом это не лечится.
    """
    if tag_path.startswith("feature:event_citations"):
        return "Move SOUR citations from event level to person/family level before export."
    if tag_path.startswith("feature:multiple_names"):
        return "Pick one primary NAME; alternates become 'Also known as' notes."
    if tag_path.startswith("feature:inline_objects"):
        return "Convert inline OBJE to top-level OBJE records before export."
    if tag_path.startswith("feature:name_variants"):
        return "Pick primary script; FONE/ROMN variants will not survive."
    if tag_path.startswith("feature:source_long_text"):
        return "Truncate or split long SOUR.TEXT/PUBL above ~248 characters."
    if tag_path.startswith("feature:citation_inline_notes"):
        return "Move citation NOTEs to top-level NOTE records before export."
    return None


def _short_label(description: str) -> str:
    """Сжать description в slug для rule_id (детерминированно, lowercase)."""
    head = description.split(".", maxsplit=1)[0]
    return "_".join(head.lower().split())[:48] or "general"


def _summarize(findings: list[AuditFinding]) -> dict[str, int]:
    """Counter findings по severity. Ключи всегда все три, даже при 0."""
    summary: dict[str, int] = {s.value: 0 for s in AuditSeverity}
    for finding in findings:
        summary[finding.severity.value] += 1
    return summary


__all__ = [
    "AuditFinding",
    "AuditSeverity",
    "ExportAudit",
    "TargetPlatform",
    "audit_export",
]
