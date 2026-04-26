"""Лексер GEDCOM-файла: текст → поток :class:`GedcomLine`.

Каждая физическая строка GEDCOM имеет вид::

    LEVEL [XREF] TAG [VALUE]

Примеры::

    0 @I1@ INDI
    1 NAME John /Smith/
    2 SURN Smith
    1 BIRT
    2 DATE 12 JAN 1850

Помимо парсинга формата, лексер **сразу склеивает** дочерние строки ``CONT``
и ``CONC`` в значение их родителя:

* ``CONT`` — добавляет ``"\\n"`` плюс свой value.
* ``CONC`` — добавляет свой value встык, без перевода строки.

Это значит, что в потоке :class:`GedcomLine` строк с тегом ``CONT``/``CONC``
**нет** — их значение уже сидит в ``value`` родительской строки. Парсер уровнем
выше работает с уже склеенным текстом и не должен помнить про эти теги.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator
from dataclasses import dataclass

from gedcom_parser.exceptions import GedcomLenientWarning, GedcomLexerError
from gedcom_parser.models import GedcomLine

# Регулярка под одну физическую строку GEDCOM.
# Группы: 1=LEVEL, 2=XREF (опционально, с @), 3=TAG, 4=VALUE (опционально).
_LINE_RE = re.compile(
    r"^"
    r"(\d+)"  # LEVEL
    r"[ \t]+"
    r"(?:(@[^@\s]+@)[ \t]+)?"  # XREF (опционально)
    r"([A-Za-z_][A-Za-z0-9_]*)"  # TAG
    r"(?:[ \t](.*))?"  # VALUE (может содержать пробелы)
    r"$"
)


# -----------------------------------------------------------------------------
# Внутренняя структура: накопитель «физических» строк до их финального yield.
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class _Pending:
    """Промежуточное состояние ещё не выданной логической строки.

    Накапливает CONT/CONC от потомков. ``value`` мутируется по ходу разбора,
    а в конце «замораживается» в неизменяемую :class:`GedcomLine`.
    """

    level: int
    xref: str | None
    tag: str
    value: str
    line_no: int

    def to_line(self) -> GedcomLine:
        return GedcomLine(
            level=self.level,
            xref=self.xref,
            tag=self.tag,
            value=self.value,
            line_no=self.line_no,
        )


# -----------------------------------------------------------------------------
# Публичный API
# -----------------------------------------------------------------------------


def iter_lines(text: str, *, lenient: bool = True) -> Iterator[GedcomLine]:
    """Разобрать текст GEDCOM в поток :class:`GedcomLine`.

    * Пустые строки и строки только из пробелов пропускаются.
    * BOM (U+FEFF) в самом начале текста съедается прозрачно.
    * Имя тега приводится к верхнему регистру (теги нечувствительны к регистру
      по спецификации).
    * Дочерние ``CONT``/``CONC`` сливаются в ``value`` родителя и в потоке не
      появляются.

    Args:
        text: Уже декодированный (см. :mod:`gedcom_parser.encoding`) текст файла.
        lenient: Если ``True`` (по умолчанию), строка, не соответствующая формату
            ``LEVEL [XREF] TAG [VALUE]``, трактуется как продолжение значения
            предыдущей логической строки (приклеивается через ``"\\n"``).
            Поведение отражает реальные экспорты MyHeritage/Geni, где длинные
            адреса и заметки переносятся на новую строку без CONT/CONC.
            Каждое такое склеивание сопровождается :class:`GedcomLenientWarning`.
            Если ``False`` — поднимается :class:`GedcomLexerError`.

    Yields:
        Поток :class:`GedcomLine` в порядке физического появления.

    Raises:
        GedcomLexerError: Если строка не парсится и (a) ``lenient=False``, либо
            (b) ``lenient=True``, но pending-строки ещё нет (некуда приклеивать).
            Также при CONT/CONC без подходящего родителя.
    """
    pending: _Pending | None = None
    # Стек последних «не-CONT/CONC» строк по уровням. stack[L] — последняя
    # строка уровня L, к которой ещё могут прилипнуть CONT/CONC её детей.
    stack: list[_Pending] = []

    # Снимаем BOM в самом начале (если декодер его не съел).
    if text.startswith("﻿"):
        text = text[1:]

    for raw_line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.rstrip("\r\n")
        # Пропуск пустых и пробельных строк.
        if not stripped.strip():
            continue

        match = _LINE_RE.match(stripped)
        if match is None:
            # Грязный реальный GEDCOM (особенно от MyHeritage и старых Geni)
            # часто переносит длинные ADDR/PLAC/NOTE-значения на новую строку
            # без CONT/CONC. Если уже есть открытая логическая строка —
            # приклеиваем «потерянное» продолжение к её value через "\n"
            # (как неявный CONT). Если pending'а нет — это правда мусор.
            if lenient and pending is not None:
                warnings.warn(
                    f"Line does not match GEDCOM format; "
                    f"appending to previous record as implicit CONT "
                    f"(line {raw_line_no}: {stripped!r})",
                    GedcomLenientWarning,
                    stacklevel=2,
                )
                pending.value = f"{pending.value}\n{stripped}"
                continue
            msg = "Cannot parse GEDCOM line"
            raise GedcomLexerError(msg, line_no=raw_line_no, snippet=stripped)

        level_str, xref, tag, value = match.groups()
        level = int(level_str)
        tag_upper = tag.upper()
        value_str = value or ""

        # ---- CONT/CONC: приклеиваем к родителю --------------------------
        if tag_upper in ("CONT", "CONC"):
            parent_level = level - 1
            if parent_level < 0 or parent_level >= len(stack):
                msg = f"{tag_upper} has no parent at level {parent_level}"
                raise GedcomLexerError(msg, line_no=raw_line_no, snippet=stripped)
            parent = stack[parent_level]
            if tag_upper == "CONT":
                parent.value = f"{parent.value}\n{value_str}"
            else:  # CONC
                parent.value = f"{parent.value}{value_str}"
            # CONT/CONC сами строки не порождают; стек не меняется.
            continue

        # ---- Обычная строка: финализируем предыдущую и заводим новую ----
        if pending is not None:
            yield pending.to_line()

        pending = _Pending(
            level=level,
            xref=xref,
            tag=tag_upper,
            value=value_str,
            line_no=raw_line_no,
        )

        # Обновляем стек: всё с уровня `level` и глубже выкидываем,
        # на позицию `level` ставим новую запись.
        del stack[level:]
        # Если уровень «прыгает» (предыдущий был 0, текущий 5) — добиваем
        # дырки самой записью, чтобы CONT/CONC у этого узла находил родителя.
        # Сами по себе дырки не проблема — невалидную иерархию поймает parser.
        while len(stack) < level:
            stack.append(pending)
        stack.append(pending)

    if pending is not None:
        yield pending.to_line()
