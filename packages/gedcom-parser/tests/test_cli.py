"""Тесты CLI ``gedcom-tool``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gedcom_parser.cli import app

runner = CliRunner()


class TestStatsCommand:
    def test_stats_minimal_output(self, minimal_ged_path: Path) -> None:
        result = runner.invoke(app, ["stats", str(minimal_ged_path)])
        assert result.exit_code == 0, result.output

        # Базовая информация присутствует.
        assert "Encoding:" in result.output
        assert "UTF-8" in result.output
        assert "Records:" in result.output

        # Счётчики тегов: 3 INDI, 1 FAM, 1 HEAD, 1 SUBM, 1 TRLR.
        assert "INDI" in result.output
        assert "FAM" in result.output
        assert "Persons:" in result.output
        assert "Families:" in result.output

    def test_stats_nonexistent_file_exits_with_error(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["stats", str(tmp_path / "missing.ged")])
        assert result.exit_code != 0


class TestParseCommand:
    def test_parse_outputs_valid_json(self, minimal_ged_path: Path) -> None:
        result = runner.invoke(app, ["parse", str(minimal_ged_path)])
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)
        assert "encoding" in payload
        assert "records" in payload
        assert payload["encoding"]["name"] == "UTF-8"
        assert len(payload["records"]) == 7

        # Проверяем структуру первой записи (HEAD).
        head = payload["records"][0]
        assert head["tag"] == "HEAD"
        assert head["level"] == 0
        assert "children" in head

    def test_parse_compact_no_indent(self, minimal_ged_path: Path) -> None:
        result = runner.invoke(app, ["parse", str(minimal_ged_path), "--compact"])
        assert result.exit_code == 0
        # Compact JSON: одна строка без отступов между ключами.
        # Проверяем отсутствие двух пробелов подряд после `{`.
        assert "{ " not in result.output

    def test_parse_writes_to_output_file(self, minimal_ged_path: Path, tmp_path: Path) -> None:
        out = tmp_path / "tree.json"
        result = runner.invoke(app, ["parse", str(minimal_ged_path), "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["encoding"]["name"] == "UTF-8"


class TestErrorHandling:
    def test_lexer_error_exits_with_code_1(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.ged"
        bad_file.write_text("INDI\n", encoding="utf-8")  # без уровня
        result = runner.invoke(app, ["stats", str(bad_file)])
        assert result.exit_code == 1
        # CliRunner по умолчанию мержит stderr и stdout в result.output.
        assert "Error" in result.output


class TestStatsEnrichment:
    """Расширенный stats показывает события, охват дат, фамилии."""

    def test_events_section(self, minimal_ged_path: Path) -> None:
        result = runner.invoke(app, ["stats", str(minimal_ged_path)])
        assert result.exit_code == 0, result.output
        assert "Events:" in result.output
        assert "dated" in result.output
        assert "placed" in result.output

    def test_date_range_shown(self, minimal_ged_path: Path) -> None:
        # minimal_ged_text содержит BIRT 1 JAN 1850.
        result = runner.invoke(app, ["stats", str(minimal_ged_path)])
        assert "1850" in result.output

    def test_top_surnames_shown(self, minimal_ged_path: Path) -> None:
        # Smith появляется у двух персон (I1, I3).
        result = runner.invoke(app, ["stats", str(minimal_ged_path)])
        assert "Top surnames" in result.output
        assert "Smith" in result.output


class TestValidateCommand:
    def test_clean_file_exit_zero(self, minimal_ged_path: Path) -> None:
        result = runner.invoke(app, ["validate", str(minimal_ged_path)])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output
        assert "Broken refs:0" in result.output

    def test_broken_refs_exit_one(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken.ged"
        bad.write_text(
            "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 FAMS @F99@\n0 TRLR\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(bad)])
        assert result.exit_code == 1
        assert "F99" in result.output
        assert "family not found" in result.output


class TestDiffCommand:
    def test_identical_files_have_no_diff(self, minimal_ged_path: Path) -> None:
        result = runner.invoke(app, ["diff", str(minimal_ged_path), str(minimal_ged_path)])
        assert result.exit_code == 0
        assert "Persons  added:   0" in result.output
        assert "Persons  removed: 0" in result.output

    def test_added_person_detected(self, tmp_path: Path, minimal_ged_path: Path) -> None:
        # Берём minimal и доклеиваем новую персону.
        plus = tmp_path / "plus.ged"
        original = minimal_ged_path.read_text(encoding="utf-8")
        # Вставим дополнительный INDI перед TRLR.
        modified = original.replace("0 TRLR\n", "0 @I999@ INDI\n1 NAME New /Person/\n0 TRLR\n")
        plus.write_text(modified, encoding="utf-8")

        result = runner.invoke(app, ["diff", str(minimal_ged_path), str(plus)])
        assert result.exit_code == 0
        assert "Persons  added:   1" in result.output
        assert "+ I999" in result.output
