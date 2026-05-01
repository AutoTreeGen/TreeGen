"""Типы diff-report'а: pydantic-модели с frozen-семантикой.

Все модели frozen и serialize'ятся через ``model_dump_json()``. Tuple-поля
(а не list) — чтобы DiffReport был hashable-friendly и его можно было
сравнить на равенство в тестах.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Все типы diff'а — снапшот результата сравнения, мутировать их незачем.
_FROZEN: ConfigDict = ConfigDict(frozen=True, extra="forbid")


class DiffOptions(BaseModel):
    """Опции, управляющие сравнением.

    Attributes:
        case_insensitive_names: Если ``True`` (по умолчанию) — имена при
            field-level diff'е сравниваются case-insensitive с trim'ом
            пробелов. Само person matching живёт по своим правилам
            (entity-resolution использует phonetic + Levenshtein).
        date_tolerance_days: Допустимая разница в днях для двух точечных
            дат. ``0`` (по умолчанию) — строгое равенство ``date_raw``.
            Применимо только когда у обеих сравниваемых дат границы
            ``date_lower == date_upper`` (single-day precision); диапазоны
            и периоды сравниваются по raw-строке.
        person_match_threshold: Композитный score, выше которого пара
            (left, right) считается одной персоной. По умолчанию ``0.85``
            (см. ADR-0015 §«Алгоритмы / Persons»).
        source_match_threshold: Порог для матчинга SOUR-записей через
            :func:`entity_resolution.sources.source_match_score`. По
            умолчанию ``0.85``.
    """

    case_insensitive_names: bool = True
    date_tolerance_days: int = Field(default=0, ge=0)
    person_match_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    source_match_threshold: float = Field(default=0.85, ge=0.0, le=1.0)

    model_config = _FROZEN


class FieldChange(BaseModel):
    """Одно изменение поля у сопоставленной сущности.

    ``left_value`` / ``right_value`` нормализованы до строк — это упрощает
    JSON-сериализацию и UI-рендер. Для multi-valued полей (например, sources)
    значения объединяются через ``", "`` в порядке встречи.
    """

    field: str
    left_value: str | None = None
    right_value: str | None = None

    model_config = _FROZEN


class PersonChange(BaseModel):
    """Сопоставленная пара персон с list of field-level diffs.

    Появляется в ``DiffReport.persons_modified`` только если ``changes``
    непустой. Чисто-matched пары без изменений в отчёт не попадают.
    """

    left_xref: str
    right_xref: str
    match_score: float = Field(ge=0.0, le=1.0)
    changes: tuple[FieldChange, ...]

    model_config = _FROZEN


class FamilyChange(BaseModel):
    """Изменение в семейной связке (``FAM``-записи).

    Семьи матчатся через сопоставление родителей: пара ``(husband, wife)``
    из left маппится в ``(husband_match, wife_match)`` через person matches.
    Если в right'е есть ``FAM`` с такой парой родителей — это matched
    family. ``left_xref``/``right_xref`` хранят xref'ы исходных FAM-записей.

    ``children_added`` / ``children_removed`` — это RIGHT-side / LEFT-side
    person xrefs соответственно (для added child reference); это упрощает
    UI-обращение к источнику данных.

    Attributes:
        left_xref: xref FAM в left, ``None`` если семья только в right
            (added).
        right_xref: xref FAM в right, ``None`` если только в left (removed).
        husband_left_xref: xref husband'а в left (для UI-рендера и audit'а).
            ``None`` если у семьи нет HUSB или семья из right (added).
        wife_left_xref: симметрично husband_left_xref для WIFE.
        children_added: xref'ы детей, которые есть в right's CHIL, но не в
            left's CHIL (после person-match переноса).
        children_removed: xref'ы детей, которые были в left's CHIL, но не
            нашлись в right's CHIL.
        description: Человеко-читаемое summary («added», «child added: I3»,
            …) для CLI/diff-print.
    """

    left_xref: str | None = None
    right_xref: str | None = None
    husband_left_xref: str | None = None
    wife_left_xref: str | None = None
    children_added: tuple[str, ...] = ()
    children_removed: tuple[str, ...] = ()
    description: str = ""

    model_config = _FROZEN


class SourceChange(BaseModel):
    """Сопоставленная пара SOUR'ов с field-level diffs."""

    left_xref: str
    right_xref: str
    match_score: float = Field(ge=0.0, le=1.0)
    changes: tuple[FieldChange, ...]

    model_config = _FROZEN


class UnknownTagChange(BaseModel):
    """Изменение в quarantined ``unknown_tags`` (Phase 5.5a).

    Для каждой стороны (added в right, removed из left) хранится owner +
    path + tag самого блока. Полное содержимое поддерева не дублируется —
    diff-report хочет быть компактным; consumer'ы достанут содержимое
    через xref-lookup в исходные документы при необходимости.
    """

    side: Literal["added", "removed"]
    owner_xref_id: str
    owner_kind: str
    path: str
    tag: str

    model_config = _FROZEN


class DiffReport(BaseModel):
    """Полный diff между двумя :class:`~gedcom_parser.document.GedcomDocument`.

    Все списки tuple'ы, ordered детерминированно (сортировка по xref),
    чтобы один и тот же ввод давал бит-в-бит одинаковый JSON.

    Attributes:
        persons_added: xref'ы из right, не сопоставленные ни с одной left
            person. Sort'нуты лексикографически.
        persons_modified: matched пары с непустым ``changes``-списком.
        persons_removed: xref'ы из left, не сопоставленные ни с одной
            right person.
        relations_added/modified/removed: семейные связи. См. FamilyChange.
        sources_added: xref'ы SOUR в right без match'а в left.
        sources_modified: matched SOUR-пары с field diff'ом.
        sources_removed: xref'ы SOUR в left без match'а в right.
        unknown_tag_changes: изменения в quarantined-блоках (Phase 5.5a).
    """

    persons_added: tuple[str, ...] = ()
    persons_modified: tuple[PersonChange, ...] = ()
    persons_removed: tuple[str, ...] = ()

    relations_added: tuple[FamilyChange, ...] = ()
    relations_modified: tuple[FamilyChange, ...] = ()
    relations_removed: tuple[FamilyChange, ...] = ()

    sources_added: tuple[str, ...] = ()
    sources_modified: tuple[SourceChange, ...] = ()
    sources_removed: tuple[str, ...] = ()

    unknown_tag_changes: tuple[UnknownTagChange, ...] = ()

    model_config = _FROZEN


__all__ = [
    "DiffOptions",
    "DiffReport",
    "FamilyChange",
    "FieldChange",
    "PersonChange",
    "SourceChange",
    "UnknownTagChange",
]
