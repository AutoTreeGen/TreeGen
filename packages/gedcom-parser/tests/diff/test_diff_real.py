"""Integration smoke на корпусе реальных GED-файлов.

Pattern мирорит ``test_smoke_corpus.py``: путь к корпусу через env var
``GEDCOM_TEST_CORPUS`` (default ``D:/Projects/GED``); если папка отсутствует —
parametrize вырождается в пустой список и pytest skip'ает тесты.

Главный invariant: ``diff_gedcoms(doc, doc) == empty DiffReport``. Если
self-diff даёт что-то непустое — алгоритм сравнения не симметричен
(нарушение: A ≡ A должно быть всегда True).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from gedcom_parser.diff import diff_gedcoms
from gedcom_parser.document import GedcomDocument
from gedcom_parser.parser import parse_file

pytestmark = pytest.mark.gedcom_real

_CORPUS_DIR = Path(os.environ.get("GEDCOM_TEST_CORPUS", "D:/Projects/GED"))


def _corpus_files() -> list[Path]:
    """``.ged`` файлы из корпуса. Пустой список — parametrize даст 0 кейсов."""
    if not _CORPUS_DIR.exists():
        return []
    # Берём только первые 3 файла — self-diff на 150 МБ-файле в CI не нужен.
    return sorted(_CORPUS_DIR.glob("*.ged"))[:3]


_CORPUS = _corpus_files()


@pytest.mark.parametrize("ged_path", _CORPUS, ids=lambda p: p.name)
def test_self_diff_is_empty(ged_path: Path) -> None:
    """``diff_gedcoms(d, d)`` для любого reпарсенного файла — пустой report."""
    records, _encoding = parse_file(ged_path)
    doc = GedcomDocument.from_records(records)

    report = diff_gedcoms(doc, doc)

    # Самосравнение должно дать пустой report по всем секциям.
    # (Документ → персон → person_match_score(p, p) = 1.0 → matched, 0 field diffs.)
    assert report.persons_added == (), f"{ged_path.name}: spurious added"
    assert report.persons_modified == (), f"{ged_path.name}: spurious modified"
    assert report.persons_removed == (), f"{ged_path.name}: spurious removed"
    assert report.relations_added == (), f"{ged_path.name}: spurious relation added"
    assert report.relations_modified == (), f"{ged_path.name}: spurious relation modified"
    assert report.relations_removed == (), f"{ged_path.name}: spurious relation removed"
    assert report.sources_added == (), f"{ged_path.name}: spurious source added"
    assert report.sources_modified == (), f"{ged_path.name}: spurious source modified"
    assert report.sources_removed == (), f"{ged_path.name}: spurious source removed"
    assert report.unknown_tag_changes == (), f"{ged_path.name}: spurious unknown_tag changes"
