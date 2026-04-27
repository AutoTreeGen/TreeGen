"""Smoke-тесты на корпусе реальных GED-файлов от разных платформ.

Корпус — отдельная папка с GEDCOM-экспортами от Ancestry, MyHeritage, Geni
и собственными деревьями владельца. Файлы разных размеров (45 KB — 150 MB),
кодировок (UTF-8, UTF-16, CP1251, ANSEL) и эпох (2002–2025).

Расположение корпуса берётся из переменной окружения ``GEDCOM_TEST_CORPUS``;
если она не задана — используется ``D:/Projects/GED`` (рабочая папка владельца).
Тесты помечены маркером ``gedcom_real`` и автоматически пропускаются, если
корпус недоступен (например, в CI).

Запуск только корпуса::

    uv run pytest packages/gedcom-parser -m gedcom_real

С другим путём::

    GEDCOM_TEST_CORPUS=/path/to/ged uv run pytest packages/gedcom-parser -m gedcom_real
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from gedcom_parser.parser import parse_file

# Все тесты в файле — на реальных данных, в CI пропускаются.
pytestmark = pytest.mark.gedcom_real


_CORPUS_DIR = Path(os.environ.get("GEDCOM_TEST_CORPUS", "D:/Projects/GED"))


def _corpus_files() -> list[Path]:
    """Собрать список ``.ged`` файлов из корпуса.

    Возвращает пустой список, если папка отсутствует — параметризованные
    тесты в этом случае не соберутся, и pytest сам отметит их как skipped.
    """
    if not _CORPUS_DIR.exists():
        return []
    return sorted(_CORPUS_DIR.glob("*.ged"))


_CORPUS = _corpus_files()


def test_corpus_dir_present() -> None:
    """Корпус доступен и содержит хотя бы один .ged."""
    if not _CORPUS_DIR.exists():
        pytest.skip(f"Corpus directory not found: {_CORPUS_DIR}")
    assert _CORPUS, f"Corpus directory {_CORPUS_DIR} contains no .ged files"


@pytest.mark.parametrize("ged_path", _CORPUS, ids=lambda p: p.name)
def test_corpus_file_parses_without_errors(ged_path: Path) -> None:
    """Каждый файл из корпуса парсится без исключений и даёт >0 записей."""
    records, encoding = parse_file(ged_path)

    assert len(records) > 0, f"No records parsed from {ged_path.name}"
    assert encoding.name, f"No encoding detected for {ged_path.name}"

    # У валидного GEDCOM первая корневая запись — HEAD, последняя — TRLR.
    # Это слабая проверка (не падать на побитых хвостах), потому проверяем мягко.
    tags = [r.tag for r in records]
    assert "HEAD" in tags, f"{ged_path.name}: no HEAD record"
    assert "TRLR" in tags, f"{ged_path.name}: no TRLR record"


# Phase 3.5 follow-up: гарантируем, что ≥1 файл в корпусе содержит ≥100
# OBJE-записей (любой формы — top-level или inline). Это canary-проверка
# того, что multimedia-парсер не падает на больших Ancestry/MyHeritage
# экспортах с тысячами фотографий.
_OBJE_COVERAGE_MIN_FILES = 1
_OBJE_COVERAGE_MIN_PER_FILE = 100


def _count_obje(records: list) -> int:  # type: ignore[type-arg]
    """Рекурсивно посчитать все OBJE-узлы в дереве (top-level и nested)."""
    count = 0
    for rec in records:
        if rec.tag == "OBJE":
            count += 1
        # Спускаемся внутрь — inline OBJE под INDI/FAM/SOUR.
        count += _count_obje(rec.children)
    return count


def test_corpus_has_files_with_substantial_obje_coverage() -> None:
    """Хотя бы один файл из корпуса содержит ≥100 OBJE.

    Это canary против регрессии «мы перестали парсить OBJE» — если ни у
    одного экспорта не оказалось ≥100 medium records, либо корпус
    деградировал, либо парсер их пропускает.
    """
    if not _CORPUS:
        pytest.skip(f"Corpus directory not found: {_CORPUS_DIR}")

    files_with_substantial_obje = 0
    obje_counts: dict[str, int] = {}
    for ged_path in _CORPUS:
        records, _encoding = parse_file(ged_path)
        n = _count_obje(records)
        obje_counts[ged_path.name] = n
        if n >= _OBJE_COVERAGE_MIN_PER_FILE:
            files_with_substantial_obje += 1

    assert files_with_substantial_obje >= _OBJE_COVERAGE_MIN_FILES, (
        f"Expected ≥{_OBJE_COVERAGE_MIN_FILES} corpus file(s) with "
        f"≥{_OBJE_COVERAGE_MIN_PER_FILE} OBJE records, got per-file counts: "
        f"{obje_counts}"
    )
