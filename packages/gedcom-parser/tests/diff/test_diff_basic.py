"""Базовые тесты на :mod:`gedcom_parser.diff`.

Покрывают спецификацию Phase 5.7a (см. ROADMAP §5.7):

* identical files → пустой report;
* persons_added / persons_removed / persons_modified;
* relations_added на уровне семьи + child added в matched family;
* unknown_tags added / removed (с переносом owner-xref'а через person matching);
* options.case_insensitive_names: True/False;
* options.date_tolerance_days;
* JSON-сериализация DiffReport (model_dump_json round-trip).

Используются как inline-GED-строки (узкие сценарии), так и hand-crafted
fixture-файлы из ``tests/fixtures/diff/small`` (реалистичный сценарий).
"""

from __future__ import annotations

import json
from pathlib import Path

from gedcom_parser.diff import (
    DiffOptions,
    DiffReport,
    diff_gedcoms,
)
from gedcom_parser.document import GedcomDocument
from gedcom_parser.parser import parse_text

# Каталог hand-crafted fixture'ов: tests/diff/__file__ → tests → fixtures/diff/small.
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "diff" / "small"


def _doc(text: str) -> GedcomDocument:
    """Распарсить GEDCOM-строку в документ."""
    return GedcomDocument.from_records(parse_text(text))


# -----------------------------------------------------------------------------
# Inline-минимум для узких сценариев.
# -----------------------------------------------------------------------------
_HEADER = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
"""


def _wrap(body: str) -> str:
    """Inline-GED: HEADER + body + TRLR."""
    return _HEADER + body + "0 TRLR\n"


# Минимальный документ: I1 John Smith, I2 Mary Jones, F1 их семья.
_MINIMAL_BODY = """\
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1 JAN 1850
1 FAMS @F1@
0 @I2@ INDI
1 NAME Mary /Jones/
1 SEX F
1 BIRT
2 DATE 5 MAR 1852
1 FAMS @F1@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
"""


def test_identical_documents_produce_empty_report() -> None:
    """Diff документа против самого себя — пустой report по всем секциям."""
    doc = _doc(_wrap(_MINIMAL_BODY))
    report = diff_gedcoms(doc, doc)

    assert report.persons_added == ()
    assert report.persons_modified == ()
    assert report.persons_removed == ()
    assert report.relations_added == ()
    assert report.relations_modified == ()
    assert report.relations_removed == ()
    assert report.sources_added == ()
    assert report.sources_modified == ()
    assert report.sources_removed == ()
    assert report.unknown_tag_changes == ()


def test_person_added_on_right() -> None:
    """Person в right без match'а в left → persons_added."""
    left = _doc(_wrap(_MINIMAL_BODY))
    right_body = (
        _MINIMAL_BODY
        + """\
0 @I3@ INDI
1 NAME Junior /Smith/
1 SEX M
1 BIRT
2 DATE 12 JUN 1880
"""
    )
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right)

    # Один new person в right.
    assert report.persons_added == ("I3",)
    # Никто не удалён, никто не модифицирован.
    assert report.persons_removed == ()
    assert report.persons_modified == ()


def test_person_removed_on_right() -> None:
    """Person в left, отсутствует в right → persons_removed."""
    body_with_extra = (
        _MINIMAL_BODY
        + """\
0 @I3@ INDI
1 NAME Junior /Smith/
1 SEX M
1 BIRT
2 DATE 12 JUN 1880
"""
    )
    left = _doc(_wrap(body_with_extra))
    right = _doc(_wrap(_MINIMAL_BODY))

    report = diff_gedcoms(left, right)

    assert report.persons_removed == ("I3",)
    assert report.persons_added == ()


def test_person_renamed_emits_field_change_name() -> None:
    """Matched person с разными NAME'ами → persons_modified[field=name]."""
    left = _doc(_wrap(_MINIMAL_BODY))

    # right: I1 переименован в Johnny (тот же surname Smith, тот же birth
    # year — должен match'нуться в person_match_score).
    right_body = _MINIMAL_BODY.replace(
        "1 NAME John /Smith/",
        "1 NAME Johnny /Smith/",
    )
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right)

    # Никто не добавлен / не удалён — оба матчатся (Smith DM-bucket + lev).
    assert report.persons_added == ()
    assert report.persons_removed == ()

    # Ровно одна модификация — у I1.
    assert len(report.persons_modified) == 1
    person_change = report.persons_modified[0]
    assert person_change.left_xref == "I1"
    fields = {c.field for c in person_change.changes}
    assert "name" in fields


def test_relation_added_when_new_family_in_right() -> None:
    """Семья в right, отсутствует в left → relations_added has 1."""
    left = _doc(_wrap(_MINIMAL_BODY))

    # right добавляет I3 (одинокий) и FX = семья только с I3 в качестве
    # husband'а — FX без match'а в left → relations_added.
    right_body = (
        _MINIMAL_BODY
        + """\
0 @I3@ INDI
1 NAME Boris /Levin/
1 SEX M
1 BIRT
2 DATE 1 JAN 1900
0 @FX@ FAM
1 HUSB @I3@
"""
    )
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right)

    # Boris добавлен в persons_added.
    assert "I3" in report.persons_added
    # FX (новая семья) — relations_added 1.
    assert len(report.relations_added) == 1
    added_fam = report.relations_added[0]
    assert added_fam.right_xref == "FX"
    assert added_fam.left_xref is None


def test_child_added_to_matched_family_relations_modified() -> None:
    """Семья matched, но в right у неё новый ребёнок → relations_modified."""
    body_with_child = (
        _MINIMAL_BODY
        + """\
0 @I3@ INDI
1 NAME Anna /Smith/
1 SEX F
1 BIRT
2 DATE 1 JAN 1880
1 FAMC @F1@
"""
    )
    left = _doc(_wrap(_MINIMAL_BODY))
    # Right: добавляем ребёнка в F1 + соответствующий INDI запись.
    right_body = body_with_child.replace(
        "0 @F1@ FAM\n1 HUSB @I1@\n1 WIFE @I2@",
        "0 @F1@ FAM\n1 HUSB @I1@\n1 WIFE @I2@\n1 CHIL @I3@",
    )
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right)

    # F1 matched (одинаковые husband+wife) → relations_modified, не added.
    assert len(report.relations_modified) == 1
    fam_change = report.relations_modified[0]
    assert fam_change.left_xref == "F1"
    assert fam_change.right_xref == "F1"
    assert "I3" in fam_change.children_added


def test_unknown_tag_added_on_right_emits_added_change() -> None:
    """``_PRIM`` под INDI в right (нет в left) → unknown_tag_changes added."""
    left = _doc(_wrap(_MINIMAL_BODY))
    # Right: I1 имеет проприетарный _PRIM (Ancestry-style).
    right_body = _MINIMAL_BODY.replace(
        "1 FAMS @F1@\n0 @I2@",
        "1 FAMS @F1@\n1 _PRIM Y\n0 @I2@",
    )
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right)

    sides = [c.side for c in report.unknown_tag_changes]
    assert "added" in sides
    added = [c for c in report.unknown_tag_changes if c.side == "added"]
    assert any(c.tag == "_PRIM" for c in added)


def test_case_insensitive_names_default_true_treats_case_as_equal() -> None:
    """Default options: name diff игнорирует casing."""
    left = _doc(_wrap(_MINIMAL_BODY))
    right_body = _MINIMAL_BODY.replace(
        "1 NAME John /Smith/",
        "1 NAME JOHN /SMITH/",
    )
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right)

    # I1 матчится; field-diff пустой потому что case_insensitive_names=True.
    person_changes_for_i1 = [c for c in report.persons_modified if c.left_xref == "I1"]
    assert person_changes_for_i1 == []


def test_case_insensitive_names_false_emits_diff() -> None:
    """``case_insensitive_names=False``: разный casing считается diff'ом."""
    left = _doc(_wrap(_MINIMAL_BODY))
    right_body = _MINIMAL_BODY.replace(
        "1 NAME John /Smith/",
        "1 NAME JOHN /SMITH/",
    )
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right, DiffOptions(case_insensitive_names=False))

    i1_changes = [c for c in report.persons_modified if c.left_xref == "I1"]
    assert len(i1_changes) == 1
    fields = {c.field for c in i1_changes[0].changes}
    assert "name" in fields


def test_date_tolerance_days_within_tolerance_no_diff() -> None:
    """`date_tolerance_days=2`: дата ±2 дня считается равной."""
    left = _doc(_wrap(_MINIMAL_BODY))
    # Right: I1 birth date 3 JAN (left was 1 JAN) — Δ 2 дня.
    right_body = _MINIMAL_BODY.replace("DATE 1 JAN 1850", "DATE 3 JAN 1850")
    right = _doc(_wrap(right_body))

    strict = diff_gedcoms(left, right, DiffOptions(date_tolerance_days=0))
    lax = diff_gedcoms(left, right, DiffOptions(date_tolerance_days=2))

    strict_i1 = [c for c in strict.persons_modified if c.left_xref == "I1"]
    lax_i1 = [c for c in lax.persons_modified if c.left_xref == "I1"]

    # Strict: birth_date — diff. Lax: birth_date в tolerance — нет diff'а.
    strict_fields = {c.field for ch in strict_i1 for c in ch.changes}
    assert "birth_date" in strict_fields

    lax_fields = {c.field for ch in lax_i1 for c in ch.changes}
    assert "birth_date" not in lax_fields


def test_diff_report_round_trips_through_json() -> None:
    """``DiffReport.model_dump_json`` → ``DiffReport.model_validate_json`` сохраняет всё."""
    left = _doc(_wrap(_MINIMAL_BODY))
    right_body = _MINIMAL_BODY.replace("1 NAME John /Smith/", "1 NAME Johnny /Smith/")
    right = _doc(_wrap(right_body))

    report = diff_gedcoms(left, right)

    payload = report.model_dump_json()
    restored = DiffReport.model_validate_json(payload)
    assert restored == report

    # Bonus sanity: JSON structurally валиден.
    parsed = json.loads(payload)
    assert "persons_modified" in parsed


# -----------------------------------------------------------------------------
# Hand-crafted fixture-файлы (5–10 персон каждый).
# -----------------------------------------------------------------------------


def test_small_fixtures_diff_matches_expected_shape() -> None:
    """Diff на hand-crafted small/{left,right}.ged fixture'ах.

    Ожидаемые изменения (см. fixture-файлы):

    * I3 → P3: NAME ``Jacob`` → ``Jakob`` + birth_place добавлено ``Pinsk``.
    * P6 (Boris Levin) — новый person в right.
    * FAM3 — новая семья в right (только с husband=P6).
    * Sources: одинаковые → matched, без diff'а.
    * Unknown tags: _FSFTID на I1 (left only) → removed; _PRIM на P3 (right
      only) → added.
    """
    left = GedcomDocument.from_records(
        parse_text((_FIXTURES_DIR / "left.ged").read_text(encoding="utf-8"))
    )
    right = GedcomDocument.from_records(
        parse_text((_FIXTURES_DIR / "right.ged").read_text(encoding="utf-8"))
    )

    report = diff_gedcoms(left, right)

    # Persons: I3 (Jacob/Smith) matched с P3 (Jakob/Smith); name+birth_place
    # должны быть в FieldChange.
    i3_changes = [c for c in report.persons_modified if c.left_xref == "I3"]
    assert len(i3_changes) == 1
    fields = {c.field for c in i3_changes[0].changes}
    assert "name" in fields
    assert "birth_place" in fields

    # P6 (Boris) — новый.
    assert "P6" in report.persons_added

    # FAM3 (новая семья) — в relations_added.
    assert any(c.right_xref == "FAM3" for c in report.relations_added)

    # Sources matched (одинаковые S1 vs S2 by title+author+abbrev),
    # field diff отсутствует.
    assert report.sources_added == ()
    assert report.sources_removed == ()
    assert report.sources_modified == ()

    # Unknown tags: _FSFTID removed (left I1 only), _PRIM added (right P3 only).
    removed_tags = {c.tag for c in report.unknown_tag_changes if c.side == "removed"}
    added_tags = {c.tag for c in report.unknown_tag_changes if c.side == "added"}
    assert "_FSFTID" in removed_tags
    assert "_PRIM" in added_tags
