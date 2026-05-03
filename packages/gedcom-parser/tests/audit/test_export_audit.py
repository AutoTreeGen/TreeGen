"""Юнит-тесты Export Audit (Phase 5.9).

Проверяют:

* для каждой target-платформы synthetic GEDCOM с известным набором
  «дроп»-тегов даёт findings с правильными rule_id и severity ``lost``;
* feature-drops (FONE/ROMN, inline-OBJE, multiple NAMEs, per-event
  citations) попадают в findings с feature-style tag_path;
* encoding warnings даются severity ``transformed`` и заполняют
  message парой ``original -> will_become``;
* structure changes даются severity ``warning`` и tag_path ``document``;
* summary'ы суммируются ровно к длине findings;
* person_id/family_id/source_id заполняются по xref-префиксу.
"""

from __future__ import annotations

import pytest
from gedcom_parser import GedcomDocument, parse_text
from gedcom_parser.audit import (
    AuditFinding,
    AuditSeverity,
    ExportAudit,
    TargetPlatform,
    audit_export,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def _doc(ged_text: str) -> GedcomDocument:
    return GedcomDocument.from_records(parse_text(ged_text))


GED_PROPRIETARY = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 _UID UID-PERSON-1
1 _FSFTID FS-PERSON-1
1 _APID Ancestry-1
0 TRLR
"""


GED_CYRILLIC = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Лев /Толстой/
2 GIVN Лев
2 SURN Толстой
0 TRLR
"""


GED_NAME_VARIANTS = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Lev /Tolstoy/
2 ROMN Lev /Tolstoj/
3 TYPE iso9
2 FONE Lev /Tolstoy/
3 TYPE russian
0 TRLR
"""


GED_INLINE_OBJE = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 OBJE
2 FILE photos/wedding.jpg
2 FORM jpeg
2 TITL Wedding 1923
0 TRLR
"""


GED_FAMILY_EVENT_CITATION = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @S1@ SOUR
1 TITL Vital Records
0 @I1@ INDI
1 NAME John /Smith/
1 FAMS @F1@
0 @I2@ INDI
1 NAME Jane /Doe/
1 FAMS @F1@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1900
2 SOUR @S1@
3 PAGE p. 42
0 TRLR
"""


# ---------------------------------------------------------------------------
# Tag-drop classification per target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", list(TargetPlatform))
def test_returns_export_audit_for_each_target(target: TargetPlatform) -> None:
    """Все 4 поддерживаемые платформы возвращают valid ExportAudit."""
    audit = audit_export(_doc(GED_PROPRIETARY), target)
    assert isinstance(audit, ExportAudit)
    assert audit.target_platform == target
    assert audit.total_records >= 1
    # summary contains all three severity keys, even when 0
    assert set(audit.summary.keys()) == {"lost", "transformed", "warning"}


def test_proprietary_tags_become_lost_findings_for_ancestry() -> None:
    """Ancestry дропает _UID/_FSFTID — findings = lost, person_id = I1."""
    audit = audit_export(_doc(GED_PROPRIETARY), TargetPlatform.ancestry)
    lost = [f for f in audit.findings if f.severity == AuditSeverity.lost]
    assert lost, "expected at least one lost finding for proprietary tags"
    paths = {f.tag_path for f in lost}
    assert "INDI._UID" in paths
    assert "INDI._FSFTID" in paths
    # все привязаны к person_id == "I1"
    for finding in lost:
        if finding.tag_path.startswith("INDI."):
            assert finding.person_id == "I1"
            assert finding.family_id is None
            assert finding.source_id is None


def test_rule_id_is_stable_per_target_and_path() -> None:
    """rule_id формат ``<target>:<kind>:<discriminator>`` — стабилен."""
    audit = audit_export(_doc(GED_PROPRIETARY), TargetPlatform.ancestry)
    for finding in audit.findings:
        assert finding.rule_id.startswith("ancestry:")
        assert finding.rule_id.count(":") >= 2


# ---------------------------------------------------------------------------
# Encoding -> transformed
# ---------------------------------------------------------------------------


def test_cyrillic_into_ancestry_yields_transformed() -> None:
    """Cyrillic NAME на Ancestry (ASCII) → severity=transformed."""
    audit = audit_export(_doc(GED_CYRILLIC), TargetPlatform.ancestry)
    transformed = [f for f in audit.findings if f.severity == AuditSeverity.transformed]
    assert transformed, "expected encoding-driven transformed findings"
    sample = transformed[0]
    assert sample.person_id == "I1"
    assert "->" in sample.message  # «original -> will_become»
    assert sample.rule_id.startswith("ancestry:encoding:")


def test_cyrillic_into_familysearch_keeps_utf8() -> None:
    """FamilySearch принимает UTF-8 — encoding-warnings = 0."""
    audit = audit_export(_doc(GED_CYRILLIC), TargetPlatform.familysearch)
    transformed = [f for f in audit.findings if f.severity == AuditSeverity.transformed]
    assert transformed == []


# ---------------------------------------------------------------------------
# Feature drops
# ---------------------------------------------------------------------------


def test_name_variants_yield_feature_lost() -> None:
    """FONE/ROMN на Ancestry — feature:name_variants как lost."""
    audit = audit_export(_doc(GED_NAME_VARIANTS), TargetPlatform.ancestry)
    feature = [f for f in audit.findings if f.tag_path.startswith("feature:name_variants")]
    assert feature
    # все feature-drops классифицируются как lost
    assert all(f.severity == AuditSeverity.lost for f in feature)
    # подсказка должна быть содержательной
    suggestion = feature[0].suggested_action
    assert suggestion is not None
    assert "script" in suggestion.lower()


def test_inline_objects_yield_feature_lost_and_warning() -> None:
    """Inline OBJE на Ancestry даёт feature:inline_objects + structure-warning."""
    audit = audit_export(_doc(GED_INLINE_OBJE), TargetPlatform.ancestry)
    feat = [f for f in audit.findings if f.tag_path.startswith("feature:inline_objects")]
    warn = [f for f in audit.findings if f.severity == AuditSeverity.warning]
    assert feat, "feature:inline_objects expected"
    assert warn, "structure warning expected"
    assert all(w.tag_path == "document" for w in warn)


def test_event_citations_attached_to_family_id() -> None:
    """Family event citations → family_id заполнен, person_id/source_id None."""
    audit = audit_export(_doc(GED_FAMILY_EVENT_CITATION), TargetPlatform.ancestry)
    feat_event = [f for f in audit.findings if f.tag_path.startswith("feature:event_citations")]
    fams = [f for f in feat_event if f.family_id is not None]
    assert fams, "expected at least one family-level event_citations finding"
    assert fams[0].family_id == "F1"
    assert fams[0].person_id is None
    assert fams[0].source_id is None


# ---------------------------------------------------------------------------
# Summary integrity
# ---------------------------------------------------------------------------


def test_summary_counts_match_findings_length() -> None:
    """Сумма summary == len(findings); ключи всегда все три."""
    audit = audit_export(_doc(GED_PROPRIETARY), TargetPlatform.ancestry)
    assert sum(audit.summary.values()) == len(audit.findings)
    assert set(audit.summary.keys()) == {s.value for s in AuditSeverity}


def test_summary_keys_present_even_when_empty() -> None:
    """Document без drops/encoding/structure всё равно даёт все три ключа."""
    minimal = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
0 TRLR
"""
    audit = audit_export(_doc(minimal), TargetPlatform.gramps)
    assert audit.summary["lost"] >= 0
    assert audit.summary["transformed"] >= 0
    assert audit.summary["warning"] >= 0
    assert sum(audit.summary.values()) == len(audit.findings)


# ---------------------------------------------------------------------------
# Read-only invariant
# ---------------------------------------------------------------------------


def test_audit_does_not_mutate_document() -> None:
    """audit_export read-only: persons/families/sources не меняются."""
    doc = _doc(GED_PROPRIETARY)
    snapshot = (
        len(doc.persons),
        len(doc.families),
        len(doc.sources),
        len(doc.unknown_tags),
    )
    audit_export(doc, TargetPlatform.ancestry)
    audit_export(doc, TargetPlatform.myheritage)
    after = (
        len(doc.persons),
        len(doc.families),
        len(doc.sources),
        len(doc.unknown_tags),
    )
    assert snapshot == after


# ---------------------------------------------------------------------------
# Finding model invariants
# ---------------------------------------------------------------------------


def test_finding_model_rejects_extra_fields() -> None:
    """``AuditFinding`` имеет ``extra='forbid'`` — защита от рандомных полей."""
    with pytest.raises(ValidationError):
        AuditFinding(
            severity=AuditSeverity.lost,
            tag_path="INDI._UID",
            rule_id="ancestry:drop:INDI._UID",
            message="x",
            unknown_field="boom",  # type: ignore[call-arg]
        )
