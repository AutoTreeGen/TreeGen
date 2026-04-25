"""Pytest-фикстуры пакета ``gedcom-parser``.

Содержит:

* ``minimal_ged_text`` / ``minimal_ged_path`` — компактный валидный GEDCOM
  5.5.5 с HEAD, SUBM, тремя INDI, FAM и TRLR. Используется большинством
  тестов парсера и CLI.
* ``cont_conc_ged_text`` — фикстура для проверки склейки CONT/CONC.
* ``personal_ged_path`` — путь к личному ``Ztree.ged`` в корне репозитория.
  Сам файл в ``.gitignore``; тесты с маркером ``gedcom_real`` пропускаются,
  если файла нет (например, в CI).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# -----------------------------------------------------------------------------
# Минимальный валидный GEDCOM 5.5.5.
#
# Семь корневых записей: HEAD, @U1@ SUBM, @I1@ INDI, @I2@ INDI, @I3@ INDI,
# @F1@ FAM, TRLR. HEAD содержит GEDC/CHAR/SOUR/SUBM/DATE как прямых детей
# (это проверяет test_walk_traverses_all_descendants).
# -----------------------------------------------------------------------------
MINIMAL_GED = """\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
1 SOUR AutoTreeGen
2 VERS 0.1.0
1 SUBM @U1@
1 DATE 25 APR 2026
0 @U1@ SUBM
1 NAME Vladimir
0 @I1@ INDI
1 NAME John /Smith/
2 GIVN John
2 SURN Smith
1 SEX M
1 BIRT
2 DATE 1 JAN 1850
2 PLAC Slonim, Russian Empire
1 FAMS @F1@
0 @I2@ INDI
1 NAME Mary /Jones/
1 SEX F
1 FAMS @F1@
0 @I3@ INDI
1 NAME Junior /Smith/
1 SEX M
1 FAMC @F1@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
0 TRLR
"""


# Фикстура для CONT/CONC. Ожидаемое склеенное value у NOTE — см.
# ``test_parser.TestContConcIntegration.test_cont_conc_collapsed_in_value``.
CONT_CONC_GED = """\
0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 NOTE This is the first physical line of a long note
2 CONT and this continues on a new line via CONT
2 CONC and this is appended without a newline via CONC
2 CONT and another CONT line at the end
0 TRLR
"""


# -----------------------------------------------------------------------------
# Фикстуры
# -----------------------------------------------------------------------------


@pytest.fixture
def minimal_ged_text() -> str:
    """Содержимое минимального GEDCOM-файла как строка."""
    return MINIMAL_GED


@pytest.fixture
def minimal_ged_path(tmp_path: Path) -> Path:
    """Минимальный GEDCOM записан во временный файл."""
    p = tmp_path / "minimal.ged"
    p.write_text(MINIMAL_GED, encoding="utf-8")
    return p


@pytest.fixture
def cont_conc_ged_text() -> str:
    """GEDCOM-фикстура с дочерними CONT/CONC у NOTE."""
    return CONT_CONC_GED


@pytest.fixture
def personal_ged_path() -> Path:
    """Путь к личному ``Ztree.ged`` в корне репозитория.

    Файл в ``.gitignore``; тесты с маркером ``gedcom_real`` сами проверяют
    наличие и пропускаются, если файла нет.
    """
    # tests/conftest.py → tests → gedcom-parser → packages → <repo-root>
    return Path(__file__).resolve().parents[3] / "Ztree.ged"
