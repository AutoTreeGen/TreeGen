"""Тесты ``GedcomDocument``: индексы и проверка ссылок."""

from __future__ import annotations

import warnings

import pytest
from gedcom_parser.document import BrokenRef, GedcomDocument
from gedcom_parser.exceptions import GedcomReferenceWarning
from gedcom_parser.parser import parse_document_file, parse_text

# -----------------------------------------------------------------------------
# Локальные фикстуры
# -----------------------------------------------------------------------------


# Минимальный GED с висячими ссылками: I1 ссылается на FAMS @F99@ (нет такой
# семьи), F1 ссылается на CHIL @I99@ (нет такой персоны).
BROKEN_REFS_GED = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 FAMS @F99@
0 @I2@ INDI
1 NAME Mary /Jones/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I99@
0 TRLR
"""


class TestFromRecords:
    def test_minimal_indexes(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)
        doc = GedcomDocument.from_records(records)

        assert doc.header is not None
        assert doc.header.gedcom_version == "5.5.5"

        assert set(doc.persons.keys()) == {"I1", "I2", "I3"}
        assert set(doc.families.keys()) == {"F1"}
        assert set(doc.submitters.keys()) == {"U1"}

        # Никаких посторонних индексов.
        assert doc.sources == {}
        assert doc.notes == {}
        assert doc.objects == {}
        assert doc.repositories == {}

    def test_get_person_and_family(self, minimal_ged_text: str) -> None:
        doc = GedcomDocument.from_records(parse_text(minimal_ged_text))
        assert doc.get_person("I1") is not None
        assert doc.get_person("I999") is None
        assert doc.get_family("F1") is not None
        assert doc.get_family("F999") is None

    def test_trlr_is_ignored(self, minimal_ged_text: str) -> None:
        # TRLR не попадает ни в какие индексы и не ломает сборку.
        doc = GedcomDocument.from_records(parse_text(minimal_ged_text))
        # Косвенный признак — все основные индексы корректные.
        assert "TRLR" not in doc.persons
        assert "TRLR" not in doc.families

    def test_duplicate_head_is_ignored(self) -> None:
        # На случай склейки/мерджа двух файлов: вторая HEAD не должна
        # затирать первую.
        text = "0 HEAD\n1 CHAR UTF-8\n1 GEDC\n2 VERS 5.5.5\n0 HEAD\n1 CHAR ANSI\n0 TRLR\n"
        doc = GedcomDocument.from_records(parse_text(text))
        assert doc.header is not None
        assert doc.header.char == "UTF-8"


# -----------------------------------------------------------------------------
# verify_references
# -----------------------------------------------------------------------------


class TestVerifyReferences:
    def test_clean_document_has_no_broken_refs(self, minimal_ged_text: str) -> None:
        doc = GedcomDocument.from_records(parse_text(minimal_ged_text))
        # SUBM @U1@ существует, FAM @F1@ существует.
        broken = doc.verify_references()
        assert broken == []

    def test_dangling_fams_and_chil_are_reported(self) -> None:
        doc = GedcomDocument.from_records(parse_text(BROKEN_REFS_GED))

        with pytest.warns(GedcomReferenceWarning):
            broken = doc.verify_references()

        targets = {(b.field, b.target_xref) for b in broken}
        assert ("FAMS", "F99") in targets
        assert ("CHIL", "I99") in targets

    def test_warn_false_is_silent(self) -> None:
        doc = GedcomDocument.from_records(parse_text(BROKEN_REFS_GED))

        # warn=False — никаких warning-ов, только список.
        with warnings.catch_warnings():
            warnings.simplefilter("error", GedcomReferenceWarning)
            # Если бы предупреждение поднялось — pytest упал бы.
            broken = doc.verify_references(warn=False)

        assert len(broken) >= 2
        # Записи — Pydantic-frozen.
        assert isinstance(broken[0], BrokenRef)

    def test_dangling_repo_in_source(self) -> None:
        text = "0 HEAD\n1 CHAR UTF-8\n0 @S1@ SOUR\n1 TITL Census\n1 REPO @R99@\n0 TRLR\n"
        doc = GedcomDocument.from_records(parse_text(text))
        broken = doc.verify_references(warn=False)
        assert any(
            b.field == "REPO" and b.target_xref == "R99" and b.expected_kind == "repository"
            for b in broken
        )

    def test_dangling_submitter_in_header(self) -> None:
        text = "0 HEAD\n1 CHAR UTF-8\n1 SUBM @U99@\n0 TRLR\n"
        doc = GedcomDocument.from_records(parse_text(text))
        broken = doc.verify_references(warn=False)
        assert any(b.owner_kind == "header" and b.target_xref == "U99" for b in broken)

    def test_dangling_event_source(self) -> None:
        text = "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Test\n1 BIRT\n2 SOUR @S99@\n0 TRLR\n"
        doc = GedcomDocument.from_records(parse_text(text))
        broken = doc.verify_references(warn=False)
        assert any(
            b.owner_xref == "I1" and "SOUR" in b.field and b.target_xref == "S99" for b in broken
        )


class TestParseDocumentFile:
    def test_parse_minimal_file(self, minimal_ged_path) -> None:  # type: ignore[no-untyped-def]
        doc = parse_document_file(minimal_ged_path)
        assert doc.encoding is not None
        assert doc.encoding.name == "UTF-8"
        assert len(doc.persons) == 3
        assert len(doc.families) == 1
        assert doc.verify_references() == []
