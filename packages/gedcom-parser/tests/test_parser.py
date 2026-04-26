"""Тесты AST-парсера и высокоуровневых обёрток."""

from __future__ import annotations

from pathlib import Path

import pytest
from gedcom_parser.exceptions import GedcomParseError
from gedcom_parser.lexer import iter_lines
from gedcom_parser.parser import (
    parse_bytes,
    parse_file,
    parse_records,
    parse_text,
)


class TestParseRecords:
    def test_parse_minimal_returns_correct_roots(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)

        # Корневые записи: HEAD, U1 (SUBM), I1, I2, I3 (INDI), F1 (FAM), TRLR.
        assert len(records) == 7

        tags = [r.tag for r in records]
        assert tags == ["HEAD", "SUBM", "INDI", "INDI", "INDI", "FAM", "TRLR"]

    def test_indi_record_structure(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)
        # Находим первого INDI (John Smith).
        john = next(r for r in records if r.xref_id == "I1")

        assert john.tag == "INDI"
        assert john.find("NAME") is not None
        assert john.find("NAME").value == "John /Smith/"  # type: ignore[union-attr]
        assert john.find("SEX").value == "M"  # type: ignore[union-attr]

        # Дата рождения — внутри BIRT.
        birt = john.find("BIRT")
        assert birt is not None
        date = birt.find("DATE")
        assert date is not None
        assert date.value == "1 JAN 1850"
        assert birt.find("PLAC").value == "Slonim, Russian Empire"  # type: ignore[union-attr]

    def test_fam_links_to_individuals(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)
        fam = next(r for r in records if r.tag == "FAM")
        assert fam.xref_id == "F1"
        assert fam.find("HUSB").value == "@I1@"  # type: ignore[union-attr]
        assert fam.find("WIFE").value == "@I2@"  # type: ignore[union-attr]
        assert fam.find("CHIL").value == "@I3@"  # type: ignore[union-attr]

    def test_walk_traverses_all_descendants(self, minimal_ged_text: str) -> None:
        records = parse_text(minimal_ged_text)
        head = records[0]
        all_nodes = list(head.walk())
        # HEAD имеет потомков GEDC, CHAR, SOUR, SUBM, DATE — и у каждого ещё свои.
        # Точное число зависит от фикстуры; важно, что walk не падает и >1.
        assert len(all_nodes) > 1
        assert all_nodes[0] is head

    def test_find_all_returns_multiple(self) -> None:
        text = "0 @I1@ INDI\n1 NAME John /Smith/\n1 NAME Jonathan /Smith/\n1 SEX M\n"
        records = parse_text(text)
        names = records[0].find_all("NAME")
        assert len(names) == 2
        assert {n.value for n in names} == {"John /Smith/", "Jonathan /Smith/"}

    def test_get_value_with_default(self) -> None:
        text = "0 @I1@ INDI\n1 NAME John /Smith/\n"
        records = parse_text(text)
        assert records[0].get_value("NAME") == "John /Smith/"
        assert records[0].get_value("NONEXISTENT") == ""
        assert records[0].get_value("NONEXISTENT", default="?") == "?"


class TestParseErrors:
    def test_level_jump_raises_in_strict_mode(self) -> None:
        # В строгом режиме прыжок 0 → 2 без промежуточного level 1 — ошибка.
        text = "0 HEAD\n2 NESTED bad\n"
        with pytest.raises(GedcomParseError, match="level"):
            parse_text(text, lenient=False)

    def test_level_jump_lenient_attaches_with_warning(self) -> None:
        # В lenient-режиме (по умолчанию) прыжок не падает: узел приклеивается
        # к верхушке стека с warning'ом. Это нужно для битых экспортов
        # MyHeritage.
        text = "0 HEAD\n2 NESTED bad\n"
        with pytest.warns(match="Level jump"):
            records = parse_text(text)
        assert records[0].tag == "HEAD"
        assert records[0].find("NESTED") is not None

    def test_starts_with_non_zero_level_raises(self) -> None:
        # Запись уровня > 0 без открытого корня неисправима даже в lenient.
        text = "1 NAME orphan\n"
        with pytest.raises(GedcomParseError):
            parse_text(text)

    def test_empty_input_returns_empty_list(self) -> None:
        assert parse_text("") == []


class TestHighLevelHelpers:
    def test_parse_bytes(self, minimal_ged_text: str) -> None:
        raw = minimal_ged_text.encode("utf-8")
        records, encoding = parse_bytes(raw)
        assert encoding.name == "UTF-8"
        assert len(records) == 7

    def test_parse_file(self, minimal_ged_path: Path) -> None:
        records, encoding = parse_file(minimal_ged_path)
        assert encoding.name == "UTF-8"
        assert len(records) == 7

    def test_parse_records_accepts_iterator(self, minimal_ged_text: str) -> None:
        # Принимает любой итератор GedcomLine.
        lines_gen = iter_lines(minimal_ged_text)
        records = parse_records(lines_gen)
        assert len(records) == 7


class TestContConcIntegration:
    def test_cont_conc_collapsed_in_value(self, cont_conc_ged_text: str) -> None:
        records = parse_text(cont_conc_ged_text)
        indi = next(r for r in records if r.tag == "INDI")
        note = indi.find("NOTE")
        assert note is not None
        # Все 4 строки сцеплены: 2 CONT (с \n) и 1 CONC (без).
        expected = (
            "This is the first physical line of a long note\n"
            "and this continues on a new line via CONT"
            "and this is appended without a newline via CONC\n"
            "and another CONT line at the end"
        )
        assert note.value == expected
