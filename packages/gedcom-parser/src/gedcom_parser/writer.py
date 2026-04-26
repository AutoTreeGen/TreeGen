"""GEDCOM 5.5.5 writer (ROADMAP §5.1.11).

Сериализует AST-уровень: список корневых :class:`GedcomRecord` → текст
GEDCOM. Симметричен парсеру:

    >>> records, _ = parse_file("tree.ged")
    >>> text = write_records(records)
    >>> parse_text(text)  # эквивалентно records (см. round-trip тесты)

Алгоритм один к одному обратный лексеру:

* Каждый узел эмитится строкой ``LEVEL [@XREF@] TAG [VALUE]``.
* Если ``value`` содержит ``"\\n"`` (склеенные на парсе ``CONT``-дети),
  для каждой следующей подстроки эмитится ``LEVEL+1 CONT chunk``. Пустая
  подстрока (``"a\\n\\nb"``) даёт пустой ``CONT`` без значения.
* ``CONC`` обратно не восстанавливается — на парсе он сливался встык
  без разделителя, и оригинальный split не сохранён. Это документировано
  и не нарушает корректности файла (просто длинная строка).
* Дочерние записи рекурсивно эмитятся с уровнем на 1 больше.

Для семантического уровня (:class:`GedcomDocument`) writer реализуется
отдельно — там нужно восстановить порядок записей и заполнить обязательные
поля HEAD; это отдельная подзадача.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gedcom_parser.models import GedcomRecord


def _emit_record(record: GedcomRecord, level: int, out: list[str]) -> None:
    """Записать узел и его потомков в ``out`` (плоский список строк)."""
    parts: list[str] = [str(level)]
    if record.xref_id is not None:
        parts.append(f"@{record.xref_id}@")
    parts.append(record.tag)

    value = record.value
    if not value:
        out.append(" ".join(parts))
    else:
        # Сплит по \n: первая часть → главная строка, остальные → CONT.
        chunks = value.split("\n")
        first = chunks[0]
        if first:
            parts.append(first)
        out.append(" ".join(parts))
        for cont_chunk in chunks[1:]:
            cont_parts: list[str] = [str(level + 1), "CONT"]
            if cont_chunk:
                cont_parts.append(cont_chunk)
            out.append(" ".join(cont_parts))

    for child in record.children:
        _emit_record(child, level + 1, out)


def write_records(records: Iterable[GedcomRecord], *, line_terminator: str = "\n") -> str:
    """Сериализовать корневые записи в GEDCOM-текст.

    Args:
        records: Корневые ``GedcomRecord`` (вывод
            :func:`gedcom_parser.parser.parse_records` или
            :func:`gedcom_parser.parser.parse_text`).
        line_terminator: Разделитель строк. По умолчанию ``"\\n"`` (LF) —
            рекомендация GEDCOM 5.5.5 для UTF-8 файлов. Для Windows-
            совместимости можно передать ``"\\r\\n"``.

    Returns:
        Готовый GEDCOM-текст с финальным ``line_terminator`` после последней
        строки. Кодирование (encode → bytes) вынесено наружу.
    """
    lines: list[str] = []
    for record in records:
        _emit_record(record, level=0, out=lines)
    return line_terminator.join(lines) + line_terminator


__all__ = ["write_records"]
