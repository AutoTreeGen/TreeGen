"""AST-парсер GEDCOM: поток ``GedcomLine`` → дерево ``GedcomRecord``.

Алгоритм — классический stack-based парсер уровневой иерархии.

На каждой логической строке:

1. Берём её ``level``.
2. Если ``level == 0`` — это новая корневая запись (HEAD, INDI, FAM, TRLR, ...).
   Сбрасываем стек и кладём в него этот узел.
3. Если ``level > 0`` — родителем будет узел на позиции ``stack[level - 1]``.
   Если такого нет (т.е. произошёл «прыжок» уровня, например 0 → 2) —
   это структурная ошибка (``GedcomParseError``).
4. Подцепляем узел к родителю и обновляем стек: всё с уровня ``level`` и выше
   удаляется, новый узел становится последним.

Результат — список корневых ``GedcomRecord``. Каждый корень содержит вложенные
``children`` нужной глубины.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from pathlib import Path

from gedcom_parser.document import GedcomDocument
from gedcom_parser.encoding import decode_gedcom, decode_gedcom_file
from gedcom_parser.exceptions import GedcomLenientWarning, GedcomParseError
from gedcom_parser.lexer import iter_lines
from gedcom_parser.models import EncodingInfo, GedcomLine, GedcomRecord


def parse_records(lines: Iterable[GedcomLine], *, lenient: bool = True) -> list[GedcomRecord]:
    """Свернуть последовательность ``GedcomLine`` в дерево записей.

    Args:
        lines: Итерируемая последовательность строк (например, из ``iter_lines``).
        lenient: Если ``True`` (по умолчанию), при «прыжке» уровня (например,
            3 → 5 без промежуточного 4) узел привязывается к верхушке стека
            и сопровождается :class:`GedcomLenientWarning`. Это бывает
            у битых экспортов MyHeritage, где promежуточные уровни теряются.
            Если ``False`` — поднимается :class:`GedcomParseError`.

            Запись уровня > 0 без любого открытого корня всё равно ошибка
            (приклеивать некуда).

    Returns:
        Список корневых ``GedcomRecord`` (по одному на каждую запись уровня 0).

    Raises:
        GedcomParseError: При не-исправимой структурной проблеме (запись
            уровня > 0 до любого корня, или прыжок при ``lenient=False``).
    """
    roots: list[GedcomRecord] = []
    # ``stack[i]`` — последний открытый узел на уровне ``i``.
    # На каждом шаге у нас всегда заполнены позиции 0..current_level.
    stack: list[GedcomRecord] = []

    for line in lines:
        node = GedcomRecord.from_line(line)

        if line.level == 0:
            roots.append(node)
            stack = [node]
            continue

        # Для уровня L нужен родитель на уровне L-1.
        if line.level - 1 >= len(stack):
            if len(stack) == 0:
                # Запись уровня > 0 до первого 0 — приклеить некуда.
                msg = (
                    f"Record at level {line.level} appears before any "
                    f"level-0 record (stack is empty)"
                )
                raise GedcomParseError(msg, line_no=line.line_no)

            if not lenient:
                msg = (
                    f"Unexpected level jump: line at level {line.level} "
                    f"has no parent at level {line.level - 1} "
                    f"(stack depth: {len(stack)})"
                )
                raise GedcomParseError(msg, line_no=line.line_no)

            # Lenient: прицепить к верхушке стека, эффективный уровень — len(stack).
            warnings.warn(
                f"Level jump at line {line.line_no}: level {line.level} has "
                f"no parent at level {line.level - 1}; attaching to top of "
                f"stack (effective level {len(stack)})",
                GedcomLenientWarning,
                stacklevel=2,
            )
            parent = stack[-1]
            parent.children.append(node)
            stack.append(node)
            continue

        parent = stack[line.level - 1]
        parent.children.append(node)

        # Обновляем стек: всё, что было глубже или на том же уровне, выкидываем.
        del stack[line.level :]
        stack.append(node)

    return roots


# -----------------------------------------------------------------------------
# Высокоуровневые удобные обёртки
# -----------------------------------------------------------------------------


def parse_text(text: str, *, lenient: bool = True) -> list[GedcomRecord]:
    """Распарсить уже декодированный текст GEDCOM.

    Параметр ``lenient`` пробрасывается в :func:`iter_lines` и
    :func:`parse_records`. По умолчанию ``True`` — реальные «грязные» файлы
    разбираются с warning-ами; при ``False`` любая невалидная строка или
    прыжок уровня поднимают исключение.
    """
    return parse_records(iter_lines(text, lenient=lenient), lenient=lenient)


def parse_bytes(raw: bytes, *, lenient: bool = True) -> tuple[list[GedcomRecord], EncodingInfo]:
    """Распарсить сырые байты GEDCOM с автоопределением кодировки.

    Returns:
        Кортеж ``(records, encoding_info)``.
    """
    text, info = decode_gedcom(raw)
    records = parse_text(text, lenient=lenient)
    return records, info


def parse_file(
    path: str | Path, *, lenient: bool = True
) -> tuple[list[GedcomRecord], EncodingInfo]:
    """Прочитать файл, определить кодировку и распарсить.

    Args:
        path: Путь к ``.ged``-файлу.
        lenient: См. :func:`parse_text`.

    Returns:
        Кортеж ``(records, encoding_info)``.
    """
    text, info = decode_gedcom_file(Path(path))
    records = parse_text(text, lenient=lenient)
    return records, info


def parse_document_file(path: str | Path, *, lenient: bool = True) -> GedcomDocument:
    """Прочитать файл и собрать семантический :class:`GedcomDocument`.

    Высокоуровневая обёртка: ``parse_file`` → :meth:`GedcomDocument.from_records`,
    с пробросом распознанной кодировки в документ.

    ``verify_references()`` НЕ вызывается автоматически — это решает вызывающий
    код (CLI ``stats``/``validate``, импортёр в БД и т.д.), чтобы парсинг
    оставался дешёвым.

    Args:
        path: Путь к ``.ged``-файлу.
        lenient: См. :func:`parse_text`.

    Returns:
        Заполненный :class:`GedcomDocument`.
    """
    records, info = parse_file(path, lenient=lenient)
    return GedcomDocument.from_records(records, encoding=info)
