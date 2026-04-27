"""Smoke-тест на личном GED-файле владельца.

Маркер ``gedcom_real`` — тест требует наличия ``D:\\Projects\\TreeGen\\Ztree.ged``
и пропускается, если файла нет (например, в CI). Запуск:

    uv run pytest packages/gedcom-parser -m gedcom_real

Цель — убедиться, что парсер выдерживает реальный «грязный» GED, а не только
синтетические фикстуры.
"""

from __future__ import annotations

import warnings
from collections import Counter
from pathlib import Path

import pytest
from gedcom_parser import GedcomDocument, parse_file
from gedcom_parser.entities import Citation, Source

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


def test_personal_ged_citation_and_source_fields_reachable(
    personal_ged_path: Path,
) -> None:
    """Phase 1.x sanity-check: на реальных данных работают Citation и Source.abbreviation.

    Не делаем сильных утверждений «должен найтись хотя бы один …» — структура
    личного дерева может меняться. Проверяем, что:

    * семантический слой собирается без исключений;
    * каждый ``Citation`` корректно типизирован (frozen Pydantic-модель с
      набором новых полей);
    * каждый ``Source`` имеет атрибут ``abbreviation`` (даже если ``None``);
    * у каждой Citation поля ``page`` / ``quality`` / ``source_xref`` —
      строки/инты/None допустимых типов.

    Этого достаточно, чтобы поймать регрессию маппинга при будущих правках
    парсера на real-world входе.
    """
    if not personal_ged_path.exists():
        pytest.skip(f"Personal GED not available at {personal_ged_path}")

    with warnings.catch_warnings():
        # Реальный файл может содержать висячие ссылки и нераспознаваемые даты.
        # Для smoke это не интересно — глушим ради чистого вывода.
        warnings.simplefilter("ignore")
        records, encoding = parse_file(personal_ged_path)
        document = GedcomDocument.from_records(records, encoding=encoding)

    # Source.abbreviation доступен на всех источниках (даже если None).
    for source in document.sources.values():
        assert isinstance(source, Source)
        assert source.abbreviation is None or isinstance(source.abbreviation, str)

    # Citations у событий и персон/семей выглядят валидно.
    citation_total = 0
    for person in document.persons.values():
        for citation in person.citations:
            _assert_citation_well_typed(citation)
            citation_total += 1
        for event in person.events:
            for citation in event.citations:
                _assert_citation_well_typed(citation)
                citation_total += 1
    for family in document.families.values():
        for citation in family.citations:
            _assert_citation_well_typed(citation)
            citation_total += 1
        for event in family.events:
            for citation in event.citations:
                _assert_citation_well_typed(citation)
                citation_total += 1

    # citation_total может быть 0 если личное дерево не цитирует источники —
    # это валидно, не fail. Главное, что обход и типизация прошли.
    assert citation_total >= 0


def _assert_citation_well_typed(citation: Citation) -> None:
    """Проверить, что Citation — frozen-модель с ожидаемыми типами полей."""
    assert isinstance(citation, Citation)
    # source_xref — либо None (inline-source), либо непустая строка без @.
    if citation.source_xref is not None:
        assert isinstance(citation.source_xref, str)
        assert "@" not in citation.source_xref
    # page и event_role — Optional[str].
    assert citation.page is None or isinstance(citation.page, str)
    assert citation.event_role is None or isinstance(citation.event_role, str)
    # quality — int 0..3 либо None.
    if citation.quality is not None:
        assert isinstance(citation.quality, int)
        assert 0 <= citation.quality <= 3
    # tuple-поля действительно tuple, а не list.
    assert isinstance(citation.notes_xrefs, tuple)
    assert isinstance(citation.notes_inline, tuple)
    assert isinstance(citation.objects_xrefs, tuple)
