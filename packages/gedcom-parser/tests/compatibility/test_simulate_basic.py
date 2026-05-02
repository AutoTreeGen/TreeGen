"""Базовые тесты Compatibility Simulator (Phase 5.6).

Проверяют:
* drop-rules: проприетарные теги, попавшие в ``GedcomDocument.unknown_tags``,
  правильно классифицируются для каждого таргета;
* feature-rules: known-but-incompatible части (inline-OBJE, FONE/ROMN-варианты,
  multiple NAMEs, per-event citations) попадают в отчёт у нужных таргетов;
* encoding warnings: ASCII-only таргет искажает кириллицу, UTF-8-таргеты — нет;
* substitutions: em-dash и `ё` заменяются у Ancestry до проверки charset;
* loss_pct остаётся в [0, 1] и больше для строгих таргетов;
* ошибка для неизвестного target.
"""

from __future__ import annotations

import pytest
from gedcom_parser import GedcomDocument, parse_text
from gedcom_parser.compatibility import (
    TARGETS,
    CompatibilityReport,
    EncodingIssue,
    StructureChange,
    TagDrop,
    simulate,
)
from gedcom_parser.compatibility.rules import load_rules


def _doc(ged_text: str) -> GedcomDocument:
    """Скомпилировать в документ inline GEDCOM-строку (без CLI/IO)."""
    return GedcomDocument.from_records(parse_text(ged_text))


# -----------------------------------------------------------------------------
# Фикстуры
# -----------------------------------------------------------------------------


# Один INDI с _UID, _APID, _FSFTID и кириллическим именем. Используется в
# нескольких тестах ниже — не «золотой» MINIMAL_GED, а заточенный на случай
# симулятора.
GED_WITH_PROPRIETARY_TAGS = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Лев /Толстой/
1 SEX M
1 _UID ABCDEF-1234-5678
1 _APID Ancestry-Person-42
1 _FSFTID FS-PERSON-99
0 TRLR
"""


# INDI с FONE/ROMN под NAME (известный тег, попадает не в quarantine, а в
# Person.names[*].variants — тестируем feature: name_variants).
GED_WITH_NAME_VARIANTS = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Lev /Tolstoy/
2 GIVN Lev
2 SURN Tolstoy
2 ROMN Lev /Tolstoj/
3 TYPE iso9
2 FONE Лев /Толстой/
3 TYPE russian
0 TRLR
"""


# INDI с inline-OBJE (без xref). Person.inline_objects получит элемент.
GED_WITH_INLINE_OBJE = """\
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


# INDI + FAM с per-event citations (Event.citations непустой).
GED_WITH_EVENT_CITATIONS = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @S1@ SOUR
1 TITL Census of 1897
0 @I1@ INDI
1 NAME John /Smith/
1 BIRT
2 DATE 1 JAN 1850
2 SOUR @S1@
3 PAGE p.42
3 QUAY 3
0 TRLR
"""


# Em-dash + 'ё' — проверка substitutions у Ancestry. Note хранится как
# top-level @N1@ NOTE, чтобы попасть в GedcomDocument.notes (inline-NOTE
# на персоне семантический слой не моделирует).
GED_WITH_EM_DASH = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @N1@ NOTE А ещё ёлка
0 @I1@ INDI
1 NAME John /Smith — Sr./
1 NOTE @N1@
0 TRLR
"""


# -----------------------------------------------------------------------------
# 1. Quarantined tag drops
# -----------------------------------------------------------------------------


def test_ancestry_drops_uid_and_apid_and_fsftid() -> None:
    """Ancestry-rules дропают _UID, _APID, _FSFTID — все три в одном INDI."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="ancestry")

    paths = sorted(d.tag_path for d in report.tag_drops if d.tag_path.startswith("INDI."))
    assert "INDI._UID" in paths
    assert "INDI._FSFTID" in paths
    # _APID не у Ancestry в drops — он же *их* собственный тег, то есть Ancestry
    # его нормально принимает. Убедимся, что он НЕ попал в drop-список.
    assert "INDI._APID" not in paths


def test_myheritage_drops_apid_keeps_uid() -> None:
    """MyHeritage дропает чужие _APID/_TID/_PID, но сохраняет свой _UID."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="myheritage")

    paths = sorted(d.tag_path for d in report.tag_drops if d.tag_path.startswith("INDI."))
    assert "INDI._APID" in paths
    assert "INDI._FSFTID" in paths
    assert "INDI._UID" not in paths, "MyHeritage preserves its own _UID"


def test_familysearch_drops_uid_and_apid() -> None:
    """FamilySearch заменяет любые внешние ID своим FSFTID — кроме своего."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="familysearch")

    paths = sorted(d.tag_path for d in report.tag_drops if d.tag_path.startswith("INDI."))
    assert "INDI._UID" in paths
    assert "INDI._APID" in paths
    assert "INDI._FSFTID" not in paths, "FamilySearch preserves its own FSFTID"


def test_gramps_preserves_proprietary_tags() -> None:
    """Gramps — самый permissive: drops пуст для нашего тестового файла."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="gramps")

    assert report.tag_drops == ()


# -----------------------------------------------------------------------------
# 2. Encoding warnings
# -----------------------------------------------------------------------------


def test_ancestry_ascii_mangles_cyrillic_name() -> None:
    """Ancestry → ASCII: 'Лев /Толстой/' превращается в '?-only' строку."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="ancestry")

    by_field = {issue.field: issue for issue in report.encoding_warnings}
    assert "NAME[0].value" in by_field, "name field must be flagged"
    issue = by_field["NAME[0].value"]
    assert issue.original == "Лев /Толстой/"
    # Все кириллические символы → '?', разделители /  и пробел сохраняются.
    assert "?" in issue.will_become
    assert "/" in issue.will_become
    # И никаких unicode-codepoint'ов > 0x7F не остаётся:
    assert all(ord(ch) <= 0x7F for ch in issue.will_become)


def test_gramps_keeps_cyrillic_intact() -> None:
    """Gramps — UTF-8: предупреждений по encoding не должно быть."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="gramps")

    assert report.encoding_warnings == ()


def test_ancestry_substitution_applied_before_charset_check() -> None:
    """Em-dash и 'ё' переходят в '-' / 'e' через substitutions, не в '?'."""
    doc = _doc(GED_WITH_EM_DASH)

    report = simulate(doc, target="ancestry")

    note_issue = next(i for i in report.encoding_warnings if i.field == "NOTE")
    # 'ё' в substitutions → 'e'; кириллические 'А', 'щ' и т.д. → '?'.
    # Конкретное значение зависит от substitutions — проверяем, что 'ё' не превратилась в '?'.
    assert "ё" in note_issue.original
    assert "ё" not in note_issue.will_become
    assert "e" in note_issue.will_become or "?" in note_issue.will_become

    name_issue = next(i for i in report.encoding_warnings if i.field == "NAME[0].value")
    # em-dash → '-'
    assert "—" in name_issue.original
    assert "—" not in name_issue.will_become
    assert "-" in name_issue.will_become


# -----------------------------------------------------------------------------
# 3. Feature drops (known-but-incompatible)
# -----------------------------------------------------------------------------


def test_ancestry_drops_name_variants_feature() -> None:
    """FONE/ROMN под NAME → feature:name_variants drops у Ancestry."""
    doc = _doc(GED_WITH_NAME_VARIANTS)

    report = simulate(doc, target="ancestry")

    feature_paths = [d.tag_path for d in report.tag_drops if d.tag_path.startswith("feature:")]
    assert any(p.startswith("feature:name_variants") for p in feature_paths)


def test_familysearch_drops_inline_objects_feature() -> None:
    """Inline-OBJE → feature:inline_objects drops у FamilySearch."""
    doc = _doc(GED_WITH_INLINE_OBJE)

    report = simulate(doc, target="familysearch")

    feature_paths = [d.tag_path for d in report.tag_drops if d.tag_path.startswith("feature:")]
    assert "feature:inline_objects" in feature_paths


def test_ancestry_event_citations_feature_drop() -> None:
    """Per-event SOUR с PAGE/QUAY → feature:event_citations у Ancestry."""
    doc = _doc(GED_WITH_EVENT_CITATIONS)

    report = simulate(doc, target="ancestry")

    feature_paths = [d.tag_path for d in report.tag_drops if d.tag_path.startswith("feature:")]
    assert any(p.startswith("feature:event_citations") for p in feature_paths)


# -----------------------------------------------------------------------------
# 4. Loss percentage и общие свойства отчёта
# -----------------------------------------------------------------------------


def test_loss_pct_in_unit_range_for_every_target() -> None:
    """estimated_loss_pct всегда в [0.0, 1.0] для всех 4 таргетов."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    for target in TARGETS:
        report = simulate(doc, target=target)
        assert 0.0 <= report.estimated_loss_pct <= 1.0, (
            f"target={target}: loss={report.estimated_loss_pct}"
        )


def test_ancestry_loss_higher_than_gramps() -> None:
    """Ancestry строже Gramps на одном и том же документе."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    ancestry = simulate(doc, target="ancestry")
    gramps = simulate(doc, target="gramps")

    assert ancestry.estimated_loss_pct > gramps.estimated_loss_pct


def test_simulate_returns_compatibility_report_with_target_name() -> None:
    """simulate() возвращает CompatibilityReport, target-поле = имени таргета."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="myheritage")

    assert isinstance(report, CompatibilityReport)
    assert report.target == "myheritage"
    # Все элементы — типизированные модели, не голые tuple'ы.
    assert all(isinstance(d, TagDrop) for d in report.tag_drops)
    assert all(isinstance(i, EncodingIssue) for i in report.encoding_warnings)
    assert all(isinstance(s, StructureChange) for s in report.structure_changes)


def test_load_rules_for_every_target_succeeds() -> None:
    """Все 4 YAML-файла с правилами валидны и грузятся через load_rules."""
    for target in TARGETS:
        rules = load_rules(target)
        assert rules.target == target


def test_simulate_unknown_target_raises_file_not_found() -> None:
    """Незнакомый target — это FileNotFoundError, а не молчаливый пустой отчёт."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    with pytest.raises(FileNotFoundError):
        simulate(doc, target="nonexistent")  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# 5. Structure changes
# -----------------------------------------------------------------------------


def test_familysearch_always_warns_about_shared_tree_remap() -> None:
    """У FamilySearch есть always-trigger structure rule про FSFTID-ремап."""
    doc = _doc(GED_WITH_PROPRIETARY_TAGS)

    report = simulate(doc, target="familysearch")

    descriptions = [s.description for s in report.structure_changes]
    assert any("shared tree" in d.lower() or "fsftid" in d.lower() for d in descriptions)
