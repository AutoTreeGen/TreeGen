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
