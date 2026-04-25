"""Семантический слой: домен GEDCOM поверх AST.

Каркас «сущностей», в которые сворачиваются корневые ``GedcomRecord``:

* :class:`Person`, :class:`Family` — основные участники LINEAGE-LINKED дерева.
* :class:`Event` — события (``BIRT``, ``DEAT``, ``MARR`` и т.д.) у персон и семей.
* :class:`Name` — имя персоны с базовым расщеплением ``/Surname/``-нотации.
* :class:`Source`, :class:`Citation`, :class:`Note`, :class:`MultimediaObject`,
  :class:`Repository`, :class:`Submitter` — ссылочные сущности.
* :class:`Header` — описание самого файла (HEAD-блок).

Связи между сущностями хранятся как **xref-строки** (например, ``"F1"``,
``"I12"``) — без обрамляющих ``@``. Это совпадает с устройством GEDCOM,
исключает циклы в Pydantic и упрощает round-trip. Резолв — через
:class:`gedcom_parser.document.GedcomDocument`.

В этой итерации все «сложные» поля (даты, места, транслитерация) хранятся
как ``*_raw`` строки. Парсинг дат — отдельная задача (ROADMAP §5.1, подпункт
5), нормализация имён — подпункт 6, мест — 7.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from gedcom_parser.models import GedcomRecord


# -----------------------------------------------------------------------------
# Базовый конфиг
# -----------------------------------------------------------------------------
# Все сущности фрозен — сущность парсера это «снапшот файла». Дальнейшая
# мутация (правка пользователем, мерж) живёт уже в shared-models с provenance.
_FROZEN: ConfigDict = ConfigDict(frozen=True, extra="forbid")


# -----------------------------------------------------------------------------
# Утилиты
# -----------------------------------------------------------------------------


def _split_name_value(value: str) -> tuple[str | None, str | None, str | None]:
    """Расщепить значение тега ``NAME`` на ``(given, surname, suffix)``.

    Формат GEDCOM: ``Given /Surname/ Suffix``, где фамилия обрамляется
    парой косых черт. Любая часть может отсутствовать; пустая часть → None.
    Если в значении меньше двух ``/``, всё считается ``given``.
    """
    first = value.find("/")
    if first != -1:
        second = value.find("/", first + 1)
        if second != -1:
            given = value[:first].strip() or None
            surname = value[first + 1 : second].strip() or None
            suffix = value[second + 1 :].strip() or None
            return given, surname, suffix
    return (value.strip() or None, None, None)


def _strip_xref(value: str) -> str:
    """Снять обрамляющие ``@`` со строки-ссылки. ``@I1@`` → ``I1``.

    Если ``@`` нет — вернуть как есть. Пустую строку — как есть.
    """
    if len(value) >= 2 and value.startswith("@") and value.endswith("@"):
        return value[1:-1]
    return value


def _xrefs_under(record: GedcomRecord, tag: str) -> tuple[str, ...]:
    """Все xref-значения у прямых потомков с заданным тегом.

    Пропускает потомков, у которых ``value`` не выглядит как ``@X@``
    (характерно для inline-notes ``1 NOTE текст…``).
    """
    out: list[str] = []
    for child in record.find_all(tag):
        if child.value.startswith("@") and child.value.endswith("@"):
            out.append(_strip_xref(child.value))
    return tuple(out)


# -----------------------------------------------------------------------------
# Имя
# -----------------------------------------------------------------------------


class Name(BaseModel):
    """Имя персоны.

    Хранит исходное значение тега ``NAME`` (`value`) и базовое расщепление
    через ``/Surname/``-нотацию. Подтеги ``GIVN`` / ``SURN`` / ``NPFX`` /
    ``NSFX`` / ``NICK``, если присутствуют, имеют приоритет над расщеплением
    из ``value`` — это поведение спецификации GEDCOM 5.5.5 §3.5 и реальных
    экспортёров.

    Глубокая нормализация (патронимы, девичьи фамилии, иврит/идиш-варианты,
    транслитерация) — задача подпунктов 6 и 8 ROADMAP §5.1.
    """

    value: str = Field(description="Сырое значение тега NAME, как в файле.")
    type_: str | None = Field(default=None, description="Подтег NAME TYPE.")
    given: str | None = None
    surname: str | None = None
    prefix: str | None = Field(default=None, description="Подтег NPFX.")
    suffix: str | None = Field(default=None, description="Подтег NSFX.")
    nickname: str | None = Field(default=None, description="Подтег NICK.")
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Name:
        """Построить ``Name`` из узла ``NAME``.

        Подтеги ``GIVN``/``SURN``/``NPFX``/``NSFX``/``NICK`` — приоритетные.
        При их отсутствии для ``given``/``surname``/``suffix`` применяется
        расщепление ``/Surname/``-нотации в ``value`` (см. :func:`_split_name_value`).
        """
        given_from_value, surname_from_value, suffix_from_value = _split_name_value(record.value)

        givn_sub = record.get_value("GIVN") or None
        surn_sub = record.get_value("SURN") or None
        nsfx_sub = record.get_value("NSFX") or None

        return cls(
            value=record.value,
            type_=record.get_value("TYPE") or None,
            given=givn_sub if givn_sub is not None else given_from_value,
            surname=surn_sub if surn_sub is not None else surname_from_value,
            prefix=record.get_value("NPFX") or None,
            suffix=nsfx_sub if nsfx_sub is not None else suffix_from_value,
            nickname=record.get_value("NICK") or None,
            line_no=record.line_no,
        )


# -----------------------------------------------------------------------------
# Событие
# -----------------------------------------------------------------------------


class Event(BaseModel):
    """Событие, привязанное к персоне или семье.

    Покрывает все стандартные теги событий GEDCOM 5.5.5 §3.5: ``BIRT``,
    ``DEAT``, ``BURI``, ``CHR``, ``BAPM``, ``MARR``, ``DIV``, ``EVEN`` и т.д.
    Сам тег события сохранён в ``tag``.

    Дата и место — в ``*_raw`` строках. Структурированный разбор — задача
    подпунктов 5 (даты) и 7 (места) ROADMAP §5.1.
    """

    tag: str = Field(description="Тег события (BIRT, DEAT, MARR, EVEN, ...).")
    date_raw: str | None = None
    place_raw: str | None = None
    type_: str | None = Field(default=None, description="Подтег TYPE.")
    age_raw: str | None = Field(default=None, description="Подтег AGE.")
    notes_xrefs: tuple[str, ...] = ()
    sources_xrefs: tuple[str, ...] = ()
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Event:
        """Построить ``Event`` из узла события."""
        date_node = record.find("DATE")
        plac_node = record.find("PLAC")
        return cls(
            tag=record.tag,
            date_raw=date_node.value if date_node is not None else None,
            place_raw=plac_node.value if plac_node is not None else None,
            type_=record.get_value("TYPE") or None,
            age_raw=record.get_value("AGE") or None,
            notes_xrefs=_xrefs_under(record, "NOTE"),
            sources_xrefs=_xrefs_under(record, "SOUR"),
            line_no=record.line_no,
        )


# -----------------------------------------------------------------------------
# Персона и семья
# -----------------------------------------------------------------------------

# Стандартные теги событий персоны. См. GEDCOM 5.5.5 §3.5 (PERSONAL_EVENT_STRUCTURE,
# INDIVIDUAL_ATTRIBUTE_STRUCTURE). EVEN — generic-событие, тоже подхватываем.
_INDI_EVENT_TAGS: frozenset[str] = frozenset(
    {
        "BIRT",
        "CHR",
        "DEAT",
        "BURI",
        "CREM",
        "ADOP",
        "BAPM",
        "BARM",
        "BASM",
        "BLES",
        "CHRA",
        "CONF",
        "FCOM",
        "ORDN",
        "NATU",
        "EMIG",
        "IMMI",
        "CENS",
        "PROB",
        "WILL",
        "GRAD",
        "RETI",
        "EVEN",
        # Атрибуты-события (имеют ту же структуру с DATE/PLAC).
        "CAST",
        "DSCR",
        "EDUC",
        "IDNO",
        "NATI",
        "NCHI",
        "NMR",
        "OCCU",
        "PROP",
        "RELI",
        "RESI",
        "SSN",
        "TITL",
        "FACT",
    }
)

# Стандартные теги событий семьи. См. GEDCOM 5.5.5 §3.5 (FAMILY_EVENT_STRUCTURE).
_FAM_EVENT_TAGS: frozenset[str] = frozenset(
    {
        "ANUL",
        "CENS",
        "DIV",
        "DIVF",
        "ENGA",
        "MARB",
        "MARC",
        "MARR",
        "MARL",
        "MARS",
        "RESI",
        "EVEN",
    }
)


class Person(BaseModel):
    """Запись ``INDI`` — одна персона.

    Семейные связи — через xref-ссылки: ``families_as_spouse`` (FAMS) и
    ``families_as_child`` (FAMC). Резолвить их в объекты :class:`Family`
    нужно через :class:`gedcom_parser.document.GedcomDocument`.
    """

    xref_id: str
    names: tuple[Name, ...] = ()
    sex: str | None = Field(default=None, description="Подтег SEX (M/F/U/X).")
    events: tuple[Event, ...] = ()
    families_as_spouse: tuple[str, ...] = Field(
        default=(),
        description="xref'ы семей из тега FAMS (без обрамляющих @).",
    )
    families_as_child: tuple[str, ...] = Field(
        default=(),
        description="xref'ы семей из тега FAMC (без обрамляющих @).",
    )
    notes_xrefs: tuple[str, ...] = ()
    sources_xrefs: tuple[str, ...] = ()
    objects_xrefs: tuple[str, ...] = ()
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Person:
        """Построить ``Person`` из узла ``INDI``."""
        if record.xref_id is None:
            msg = f"INDI record without xref at line {record.line_no}"
            raise ValueError(msg)

        names = tuple(Name.from_record(c) for c in record.find_all("NAME"))
        events = tuple(Event.from_record(c) for c in record.children if c.tag in _INDI_EVENT_TAGS)

        return cls(
            xref_id=record.xref_id,
            names=names,
            sex=record.get_value("SEX") or None,
            events=events,
            families_as_spouse=_xrefs_under(record, "FAMS"),
            families_as_child=_xrefs_under(record, "FAMC"),
            notes_xrefs=_xrefs_under(record, "NOTE"),
            sources_xrefs=_xrefs_under(record, "SOUR"),
            objects_xrefs=_xrefs_under(record, "OBJE"),
            line_no=record.line_no,
        )


class Family(BaseModel):
    """Запись ``FAM`` — семейная единица.

    Стороны и дети — xref-строки. Сами объекты резолвятся через
    :class:`gedcom_parser.document.GedcomDocument`.
    """

    xref_id: str
    husband_xref: str | None = None
    wife_xref: str | None = None
    children_xrefs: tuple[str, ...] = ()
    events: tuple[Event, ...] = ()
    notes_xrefs: tuple[str, ...] = ()
    sources_xrefs: tuple[str, ...] = ()
    objects_xrefs: tuple[str, ...] = ()
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Family:
        """Построить ``Family`` из узла ``FAM``."""
        if record.xref_id is None:
            msg = f"FAM record without xref at line {record.line_no}"
            raise ValueError(msg)

        husb = record.find("HUSB")
        wife = record.find("WIFE")
        events = tuple(Event.from_record(c) for c in record.children if c.tag in _FAM_EVENT_TAGS)

        return cls(
            xref_id=record.xref_id,
            husband_xref=_strip_xref(husb.value) if husb is not None and husb.value else None,
            wife_xref=_strip_xref(wife.value) if wife is not None and wife.value else None,
            children_xrefs=_xrefs_under(record, "CHIL"),
            events=events,
            notes_xrefs=_xrefs_under(record, "NOTE"),
            sources_xrefs=_xrefs_under(record, "SOUR"),
            objects_xrefs=_xrefs_under(record, "OBJE"),
            line_no=record.line_no,
        )


# -----------------------------------------------------------------------------
# Источники, заметки, медиа, репозитории, отправители
# -----------------------------------------------------------------------------


class Source(BaseModel):
    """Запись ``SOUR`` верхнего уровня — источник."""

    xref_id: str
    title: str | None = Field(default=None, description="Подтег TITL.")
    author: str | None = Field(default=None, description="Подтег AUTH.")
    publication: str | None = Field(default=None, description="Подтег PUBL.")
    repository_xref: str | None = Field(
        default=None, description="xref репозитория из подтега REPO."
    )
    text: str | None = Field(default=None, description="Подтег TEXT.")
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Source:
        """Построить ``Source`` из узла ``SOUR``."""
        if record.xref_id is None:
            msg = f"SOUR record without xref at line {record.line_no}"
            raise ValueError(msg)

        repo = record.find("REPO")
        repo_xref: str | None = None
        if repo is not None and repo.value.startswith("@") and repo.value.endswith("@"):
            repo_xref = _strip_xref(repo.value)

        return cls(
            xref_id=record.xref_id,
            title=record.get_value("TITL") or None,
            author=record.get_value("AUTH") or None,
            publication=record.get_value("PUBL") or None,
            repository_xref=repo_xref,
            text=record.get_value("TEXT") or None,
            line_no=record.line_no,
        )


class Note(BaseModel):
    """Запись ``NOTE`` верхнего уровня (с xref) — текстовая заметка.

    Inline-notes (``1 NOTE текст…`` без xref) в индекс :class:`GedcomDocument`
    не попадают — они остаются на месте у родительской сущности.
    """

    xref_id: str
    text: str
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Note:
        """Построить ``Note`` из узла ``NOTE`` с xref."""
        if record.xref_id is None:
            msg = f"NOTE record without xref at line {record.line_no}"
            raise ValueError(msg)
        return cls(xref_id=record.xref_id, text=record.value, line_no=record.line_no)


class MultimediaObject(BaseModel):
    """Запись ``OBJE`` верхнего уровня — медиа-объект (фото, скан, документ).

    GEDCOM 5.5.5 допускает несколько ``FILE`` под одним ``OBJE``. В этой
    итерации сохраняем только первый — расширим, когда понадобится.
    """

    xref_id: str
    file: str | None = Field(default=None, description="Подтег FILE (путь/URL).")
    format_: str | None = Field(default=None, description="Подтег FORM (jpg, png, pdf, ...).")
    title: str | None = Field(default=None, description="Подтег TITL.")
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> MultimediaObject:
        """Построить ``MultimediaObject`` из узла ``OBJE``."""
        if record.xref_id is None:
            msg = f"OBJE record without xref at line {record.line_no}"
            raise ValueError(msg)

        file_node = record.find("FILE")
        format_value: str | None = None
        if file_node is not None:
            # FORM может быть как под FILE, так и под OBJE напрямую.
            form_under_file = file_node.find("FORM")
            if form_under_file is not None:
                format_value = form_under_file.value or None
        if format_value is None:
            format_value = record.get_value("FORM") or None

        return cls(
            xref_id=record.xref_id,
            file=file_node.value if file_node is not None else None,
            format_=format_value,
            title=record.get_value("TITL") or None,
            line_no=record.line_no,
        )


class Repository(BaseModel):
    """Запись ``REPO`` — архив/библиотека/хранилище источников."""

    xref_id: str
    name: str | None = Field(default=None, description="Подтег NAME.")
    address_raw: str | None = Field(
        default=None, description="Подтег ADDR (полный текст без разбора)."
    )
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Repository:
        """Построить ``Repository`` из узла ``REPO``."""
        if record.xref_id is None:
            msg = f"REPO record without xref at line {record.line_no}"
            raise ValueError(msg)
        return cls(
            xref_id=record.xref_id,
            name=record.get_value("NAME") or None,
            address_raw=record.get_value("ADDR") or None,
            line_no=record.line_no,
        )


class Submitter(BaseModel):
    """Запись ``SUBM`` — отправитель файла."""

    xref_id: str
    name: str | None = Field(default=None, description="Подтег NAME.")
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Submitter:
        """Построить ``Submitter`` из узла ``SUBM``."""
        if record.xref_id is None:
            msg = f"SUBM record without xref at line {record.line_no}"
            raise ValueError(msg)
        return cls(
            xref_id=record.xref_id,
            name=record.get_value("NAME") or None,
            line_no=record.line_no,
        )


# -----------------------------------------------------------------------------
# Шапка файла
# -----------------------------------------------------------------------------


class Header(BaseModel):
    """Запись ``HEAD`` — метаданные файла (источник, версия, кодировка, ...).

    Часть полей дублирует :class:`gedcom_parser.models.EncodingInfo` — но
    ``EncodingInfo`` отражает фактически использованную кодировку (после
    BOM/heuristic), а ``Header.char`` — кодировку, **заявленную** автором
    файла. Они могут различаться.
    """

    gedcom_version: str | None = Field(default=None, description="GEDC > VERS.")
    gedcom_form: str | None = Field(default=None, description="GEDC > FORM.")
    char: str | None = Field(default=None, description="Сырое значение CHAR.")
    source_system: str | None = Field(default=None, description="Значение SOUR.")
    source_version: str | None = Field(default=None, description="SOUR > VERS.")
    source_name: str | None = Field(default=None, description="SOUR > NAME.")
    submitter_xref: str | None = None
    date_raw: str | None = None
    line_no: int | None = None

    model_config = _FROZEN

    @classmethod
    def from_record(cls, record: GedcomRecord) -> Header:
        """Построить ``Header`` из узла ``HEAD``."""
        gedc = record.find("GEDC")
        sour = record.find("SOUR")
        subm = record.find("SUBM")

        def _sub_value(parent: GedcomRecord | None, tag: str) -> str | None:
            if parent is None:
                return None
            return parent.get_value(tag) or None

        return cls(
            gedcom_version=_sub_value(gedc, "VERS"),
            gedcom_form=_sub_value(gedc, "FORM"),
            char=record.get_value("CHAR") or None,
            source_system=sour.value if sour is not None else None,
            source_version=_sub_value(sour, "VERS"),
            source_name=_sub_value(sour, "NAME"),
            submitter_xref=(
                _strip_xref(subm.value) if subm is not None and subm.value.startswith("@") else None
            ),
            date_raw=record.get_value("DATE") or None,
            line_no=record.line_no,
        )


__all__ = [
    "Event",
    "Family",
    "Header",
    "MultimediaObject",
    "Name",
    "Note",
    "Person",
    "Repository",
    "Source",
    "Submitter",
]
