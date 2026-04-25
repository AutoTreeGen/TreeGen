"""Smoke-тест на личном GED-файле владельца.

Маркер ``gedcom_real`` — тест требует наличия ``D:\\Projects\\TreeGen\\Ztree.ged``
и пропускается, если файла нет (например, в CI). Запуск:

    uv run pytest packages/gedcom-parser -m gedcom_real

Цель — убедиться, что парсер выдерживает реальный «грязный» GED, а не только
синтетические фикстуры.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from gedcom_parser.parser import parse_file

# Маркер для всех тестов в файле.
pytestmark = pytest.mark.gedcom_real


def test_parses_personal_ged_without_errors(personal_ged_path: Path) -> None:
    """Реальный личный GED парсится без исключений."""
    if not personal_ged_path.exists():
        pytest.skip(f"Personal GED not available at {personal_ged_path}")

    records, encoding = parse_file(personal_ged_path)
    assert len(records) > 0
    assert encoding.name  # любая валидная кодировка


def test_personal_ged_has_indi_records(personal_ged_path: Path) -> None:
    """В личном дереве должна быть хотя бы одна персона."""
    if not personal_ged_path.exists():
        pytest.skip(f"Personal GED not available at {personal_ged_path}")

    records, _ = parse_file(personal_ged_path)
    counts: Counter[str] = Counter(r.tag for r in records)
    assert counts.get("INDI", 0) > 0, "В дереве не найдено ни одного INDI"


def test_personal_ged_walk_works(personal_ged_path: Path) -> None:
    """Обход дерева не падает на реальных данных."""
    if not personal_ged_path.exists():
        pytest.skip(f"Personal GED not available at {personal_ged_path}")

    records, _ = parse_file(personal_ged_path)
    total_nodes = sum(1 for r in records for _ in r.walk())
    assert total_nodes > 0
