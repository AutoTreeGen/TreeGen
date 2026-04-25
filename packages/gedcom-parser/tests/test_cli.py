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

    def test_parse_writes_to_output_file(
        self, minimal_ged_path: Path, tmp_path: Path
    ) -> None:
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
