"""Pydantic-модели разобранного GEDCOM.

Три ключевые сущности:

* :class:`EncodingInfo` — результат определения кодировки файла.
* :class:`GedcomLine` — одна логическая строка (после склейки CONT/CONC).
* :class:`GedcomRecord` — узел AST: одна логическая строка плюс её дети.

Модель ``GedcomRecord`` НЕ хранит ссылку на исходную ``GedcomLine`` —
поля ``level``/``tag``/``value``/``line_no``/``xref_id`` копируются прямо
в запись. Это даёт чистую JSON-сериализацию без вложенного ``line``-объекта
(см. ``test_cli.test_parse_outputs_valid_json``: ``payload["records"][0]["tag"]``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EncodingInfo(BaseModel):
    """Результат определения кодировки GEDCOM-файла.

    Атрибуты:
        name: Каноническое имя кодировки для ``bytes.decode`` (``"UTF-8"``,
            ``"CP1251"``, ``"ANSEL"`` и т.д.).
        confidence: Оценка уверенности в диапазоне [0.0, 1.0].
        method: Как именно определена кодировка.
        head_char_raw: Сырое значение из строки ``1 CHAR ...`` HEAD-блока,
            если использовался метод ``head_char``. Иначе ``None``.
    """

    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    method: Literal["bom", "head_char", "heuristic"]
    head_char_raw: str | None = None

    model_config = ConfigDict(frozen=True)


class GedcomLine(BaseModel):
    """Одна логическая строка GEDCOM (после склейки CONT/CONC).

    Атрибуты:
        level: Уровень вложенности (0..99).
        xref: ID объекта вместе с обрамляющими ``@`` (например, ``"@I1@"``)
            или ``None``. Только для строк уровня 0 со ссылочным ID.
        tag: Стандартный или проприетарный тег (``INDI``, ``NAME``, ``BIRT`` …),
            всегда в верхнем регистре.
        value: Значение строки. Если у строки были дочерние CONT/CONC, их
            значения уже склеены сюда (CONT — через ``\\n``, CONC — встык).
        line_no: Номер физической строки, с которой начинается логическая
            (1-based). Для отладки и сообщений об ошибках.
    """

    level: int = Field(ge=0, le=99)
    xref: str | None = None
    tag: str
    value: str = ""
    line_no: int = Field(ge=1)

    model_config = ConfigDict(frozen=True)


class GedcomRecord(BaseModel):
    """Узел AST: логическая строка + её дочерние узлы.

    Поля скопированы из ``GedcomLine``, чтобы ``model_dump()`` давал плоский
    JSON без обёртки ``{"line": {...}, "children": [...]}``. Создавать удобнее
    через :meth:`from_line`.

    Атрибуты:
        level: Уровень вложенности (от исходной строки).
        xref_id: Идентификатор объекта без обрамляющих ``@`` (``"I1"`` для
            строки ``0 @I1@ INDI``). ``None``, если xref не задан.
        tag: Тег (всегда uppercase).
        value: Значение строки (с уже применёнными CONT/CONC).
        line_no: Номер исходной физической строки.
        children: Дочерние узлы.
    """

    level: int = Field(ge=0, le=99)
    xref_id: str | None = None
    tag: str
    value: str = ""
    line_no: int = Field(ge=1)
    children: list[GedcomRecord] = Field(default_factory=list)

    model_config = ConfigDict(frozen=False)

    @classmethod
    def from_line(cls, line: GedcomLine) -> GedcomRecord:
        """Создать узел из ``GedcomLine`` (без детей)."""
        # `xref` у Line содержит обрамляющие @, в record храним без них.
        xref_id = line.xref.strip("@") if line.xref else None
        return cls(
            level=line.level,
            xref_id=xref_id,
            tag=line.tag,
            value=line.value,
            line_no=line.line_no,
            children=[],
        )

    # ----- Поиск дочерних узлов -----------------------------------------

    def find(self, tag: str) -> GedcomRecord | None:
        """Первый прямой потомок с указанным тегом или ``None``."""
        tag_upper = tag.upper()
        return next((c for c in self.children if c.tag == tag_upper), None)

    def find_all(self, tag: str) -> list[GedcomRecord]:
        """Все прямые потомки с указанным тегом (в порядке встречи)."""
        tag_upper = tag.upper()
        return [c for c in self.children if c.tag == tag_upper]

    def get_value(self, tag: str, default: str = "") -> str:
        """Значение первого прямого потомка с тегом ``tag``.

        Если потомка нет — возвращает ``default`` (по умолчанию пустая строка).
        Удобно для частого паттерна ``record.find("NAME").value``.
        """
        node = self.find(tag)
        return node.value if node is not None else default

    # ----- Обход -------------------------------------------------------

    def walk(self) -> Iterator[GedcomRecord]:
        """Pre-order DFS по поддереву, начиная с самого узла.

        Гарантия: ``next(self.walk()) is self``.
        """
        yield self
        for child in self.children:
            yield from child.walk()


class RawTagBlock(BaseModel):
    """Quarantined unknown / proprietary tag (Phase 5.5a).

    Когда семантический слой (:mod:`gedcom_parser.entities`) собирает
    типизированные сущности из AST, он по whitelist'у consumes только
    ожидаемые подтеги. Всё остальное — проприетарные расширения
    (``_FSFTID`` / ``_UID`` / ``_PRIM`` / ``_TYPE``), новые GEDCOM 7.0
    теги без 5.5.5-аналога, witnesses / godparents в нестандартной
    позиции — раньше дропалось при export'е из БД.

    ``RawTagBlock`` сохраняет такой подтег **вместе со всем поддеревом**
    в ``GedcomDocument.unknown_tags``. На export builder возвращает блок
    в нужное место, гарантируя a→DB→a' round-trip без потерь.

    Attributes:
        owner_xref_id: xref верхнеуровневой записи, в которой нашли тег
            (``"I1"`` / ``"F2"`` / ``"S3"`` / ``"HEAD"``). Без обрамляющих
            ``@``.
        owner_kind: Тип записи-владельца (``"individual"`` / ``"family"``
            / ``"source"`` / ``"note"`` / ``"object"`` / ``"repository"``
            / ``"submitter"`` / ``"header"``).
        path: Dotted-путь parent-тегов внутри owner. Пустая строка =
            прямой потомок owner (``"INDI._FSFTID"`` → path=""); ``"BIRT"``
            = подтег внутри ``BIRT`` (``"BIRT._FOO"`` → path="BIRT");
            ``"BIRT.SOUR"`` = вложен ещё глубже. Path позволяет export
            builder'у вернуть блок ровно туда, откуда он пришёл, без
            наблюдения порядка children.
        record: Полное поддерево квaрантиненного тега (с тегом, value
            и всеми вложенными children). Уровень внутри ``record``
            начинается с того же значения, что был в исходном файле —
            export builder адаптирует уровень при re-injection.
    """

    owner_xref_id: str = Field(min_length=1)
    owner_kind: str = Field(min_length=1)
    path: str = Field(default="")
    record: GedcomRecord

    model_config = ConfigDict(frozen=True)
