"""Тесты writer'а: эмит и round-trip parse↔write."""

from __future__ import annotations

from gedcom_parser.parser import parse_text
from gedcom_parser.writer import write_records

# -----------------------------------------------------------------------------
# Базовые формы строк
# -----------------------------------------------------------------------------


class TestBasicEmit:
    def test_record_no_value(self) -> None:
        records = parse_text("0 HEAD\n0 TRLR\n")
        out = write_records(records)
        assert out == "0 HEAD\n0 TRLR\n"

    def test_record_with_value(self) -> None:
        records = parse_text("0 @I1@ INDI\n1 NAME John /Smith/\n")
        out = write_records(records)
        assert out == "0 @I1@ INDI\n1 NAME John /Smith/\n"

    def test_xref_round_trip(self) -> None:
        # @ оборачиваются обратно вокруг xref_id.
        records = parse_text("0 @F1@ FAM\n1 HUSB @I1@\n")
        out = write_records(records)
        assert "0 @F1@ FAM" in out
        assert "1 HUSB @I1@" in out

    def test_nested_children(self) -> None:
        text = "0 HEAD\n1 GEDC\n2 VERS 5.5.5\n2 FORM LINEAGE-LINKED\n"
        records = parse_text(text)
        out = write_records(records)
        assert out == text


# -----------------------------------------------------------------------------
# CONT / CONC
# -----------------------------------------------------------------------------


class TestContEmission:
    def test_cont_split_back(self) -> None:
        # Парсер склеит CONT в значение через "\n"; writer должен разрезать обратно.
        text = "0 @N1@ NOTE first line\n1 CONT second line\n1 CONT third line\n"
        records = parse_text(text)
        out = write_records(records)
        assert out == text

    def test_conc_collapses_to_single_line(self) -> None:
        # CONC обратно не восстанавливается (документировано). После round-trip
        # значение становится длинной одиночной строкой.
        text = "0 @N1@ NOTE part1\n1 CONC part2\n"
        records = parse_text(text)
        out = write_records(records)
        assert out == "0 @N1@ NOTE part1part2\n"

    def test_empty_cont_line(self) -> None:
        # Пустой CONT (пустая строка внутри значения) → "\n" в значении →
        # пустой CONT обратно.
        text = "0 @N1@ NOTE first\n1 CONT\n1 CONT third\n"
        records = parse_text(text)
        out = write_records(records)
        assert out == text


# -----------------------------------------------------------------------------
# Round-trip: parse → write → parse даёт ту же AST
# -----------------------------------------------------------------------------


class TestRoundTrip:
    def test_minimal_round_trip(self, minimal_ged_text: str) -> None:
        records1 = parse_text(minimal_ged_text)
        out = write_records(records1)
        records2 = parse_text(out)
        assert _serialize_ast(records1) == _serialize_ast(records2)

    def test_minimal_text_byte_equal(self, minimal_ged_text: str) -> None:
        # Для «чистого» файла (без CONC) ожидаем побайтовое равенство.
        records = parse_text(minimal_ged_text)
        assert write_records(records) == minimal_ged_text

    def test_cont_conc_round_trip(self, cont_conc_ged_text: str) -> None:
        # Round-trip AST: parse → write → parse даёт тот же набор записей,
        # даже если первый раунд write «слил» CONC в одну строку.
        records1 = parse_text(cont_conc_ged_text)
        out = write_records(records1)
        records2 = parse_text(out)
        assert _serialize_ast(records1) == _serialize_ast(records2)


def _serialize_ast(records: list) -> list[tuple]:  # type: ignore[type-arg]
    """Снять «отпечаток» AST для сравнения двух разборов.

    Включает level (по позиции в дереве), xref, tag, value и потомков
    рекурсивно. Игнорирует ``line_no`` (он отличается после round-trip).
    """

    def snap(rec, level):  # type: ignore[no-untyped-def]
        return (
            level,
            rec.xref_id,
            rec.tag,
            rec.value,
            tuple(snap(c, level + 1) for c in rec.children),
        )

    return [snap(r, 0) for r in records]


# -----------------------------------------------------------------------------
# Line terminator
# -----------------------------------------------------------------------------


class TestLineTerminator:
    def test_crlf_terminator(self) -> None:
        records = parse_text("0 HEAD\n0 TRLR\n")
        out = write_records(records, line_terminator="\r\n")
        assert out == "0 HEAD\r\n0 TRLR\r\n"
