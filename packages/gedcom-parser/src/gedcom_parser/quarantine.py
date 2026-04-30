"""Quarantine для проприетарных / неизвестных GEDCOM-тегов (Phase 5.5a).

Семантический слой (:mod:`gedcom_parser.entities`) собирает типизированные
сущности из AST whitelist-driven: каждая фабрика ``from_record`` consumes
только ожидаемые подтеги, всё остальное — просто игнорируется. Для
international cross-platform round-trip это критическая дыра: проприетарные
теги Ancestry (``_FSFTID``, ``_PRIM``, ``_TYPE``), MyHeritage (``_UID``),
Geni (``_PUBLIC``, ``_LIVING``), а также witnesses / godparents в нестандартных
позициях — все молча дропаются на export.

Quarantine идёт **whitelist-first**: для каждого верхнеуровневого record'а
(INDI/FAM/SOUR/...) известен набор тегов, которые семантический слой реально
consumes; всё остальное переезжает в :class:`gedcom_parser.models.RawTagBlock`
и сохраняется в ``GedcomDocument.unknown_tags``.

Дизайн whitelist'а:

* **Корневой набор per kind.** Прямые children'ы верхнеуровневого record'а:
  каждый тег явно классифицирован known/unknown.
* **Транзитивность.** Если top-level child known (например, ``BIRT``), то
  его поддерево quarantine **не трогает** — оно полностью consumes
  семантическим слоем (Event / Citation / etc.). Любые проприетарные теги
  вложенные внутрь known top-level child'а на этой итерации игнорируются:
  они почти всегда специфичны для конкретного event'а и реальный round-trip
  для них потребует пер-event whitelist'а — это Phase 5.5b или 5.6.
  Документировано в ADR.
* **Path = "".** Все RawTagBlock'и текущего phase 5.5a имеют ``path=""``
  (прямой потомок owner'а). Глубже — зарезервировано на 5.5b.

Пример входа (Ancestry export):

.. code-block:: text

    0 @I1@ INDI
    1 NAME John /Smith/
    1 SEX M
    1 _FSFTID 12345-ABC      ← unknown, quarantined (path="")
    1 BIRT
    2 DATE 1850
    2 _PRIM Y                ← внутри known BIRT, на 5.5a игнорируем
    1 _UID ABCDEF...         ← unknown, quarantined (path="")

После quarantine ``GedcomDocument.unknown_tags`` содержит две записи
(``_FSFTID`` и ``_UID``), каждая с full subtree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from gedcom_parser.models import RawTagBlock

if TYPE_CHECKING:
    from gedcom_parser.models import GedcomRecord

# -----------------------------------------------------------------------------
# Whitelist'ы тегов, которые consumes семантический слой.
# -----------------------------------------------------------------------------
# Каждое множество описывает прямые потомки top-level record'а соответствующего
# типа. Source of truth — фабрики ``*.from_record`` в ``entities.py``;
# изменения там должны сопровождаться обновлением whitelist'а здесь
# (test_quarantine_whitelist_matches_entities проверяет invariant).

# Стандартные индивидуальные событийные теги (см. ``entities._INDI_EVENT_TAGS``).
_INDI_EVENT_TAGS: Final[frozenset[str]] = frozenset(
    {
        "BIRT", "CHR", "DEAT", "BURI", "CREM", "ADOP", "BAPM", "BARM", "BASM",
        "BLES", "CHRA", "CONF", "FCOM", "ORDN", "NATU", "EMIG", "IMMI", "CENS",
        "PROB", "WILL", "GRAD", "RETI", "EVEN",
        "CAST", "DSCR", "EDUC", "IDNO", "NATI", "NCHI", "NMR", "OCCU", "PROP",
        "RELI", "RESI", "SSN", "TITL", "FACT",
    }
)  # fmt: skip

_FAM_EVENT_TAGS: Final[frozenset[str]] = frozenset(
    {"ANUL", "CENS", "DIV", "DIVF", "ENGA", "MARB", "MARC", "MARR", "MARL", "MARS", "RESI", "EVEN"}
)

#: Прямые children INDI, которые consumes ``Person.from_record``.
KNOWN_INDI_TAGS: Final[frozenset[str]] = frozenset(
    {"NAME", "SEX", "FAMS", "FAMC", "NOTE", "SOUR", "OBJE", *_INDI_EVENT_TAGS}
)

#: Прямые children FAM, которые consumes ``Family.from_record``.
KNOWN_FAM_TAGS: Final[frozenset[str]] = frozenset(
    {"HUSB", "WIFE", "CHIL", "NOTE", "SOUR", "OBJE", *_FAM_EVENT_TAGS}
)

#: Прямые children SOUR (top-level), consumes ``Source.from_record``.
KNOWN_SOUR_TAGS: Final[frozenset[str]] = frozenset(
    {"TITL", "AUTH", "PUBL", "ABBR", "TEXT", "REPO", "DATA", "NOTE", "OBJE"}
)

#: Прямые children NOTE (top-level), consumes ``Note.from_record``.
KNOWN_NOTE_TAGS: Final[frozenset[str]] = frozenset({"CONC", "CONT", "SOUR"})

#: Прямые children OBJE (top-level), consumes ``MultimediaObject.from_record``.
KNOWN_OBJE_TAGS: Final[frozenset[str]] = frozenset(
    {"FILE", "FORM", "TITL", "NOTE", "SOUR", "_FILE"}
)

#: Прямые children REPO, consumes ``Repository.from_record``.
KNOWN_REPO_TAGS: Final[frozenset[str]] = frozenset({"NAME", "ADDR", "PHON", "EMAIL", "WWW", "NOTE"})

#: Прямые children SUBM, consumes ``Submitter.from_record``.
KNOWN_SUBM_TAGS: Final[frozenset[str]] = frozenset(
    {"NAME", "ADDR", "PHON", "EMAIL", "WWW", "LANG", "RFN", "RIN", "NOTE"}
)

#: Прямые children HEAD, consumes ``Header.from_record``.
KNOWN_HEAD_TAGS: Final[frozenset[str]] = frozenset(
    {"GEDC", "SOUR", "DEST", "DATE", "SUBM", "SUBN", "FILE", "COPR", "CHAR", "LANG", "NOTE"}
)

#: Mapping ``top-level tag → known children whitelist``. Используется для
#: классификации known/unknown в ``quarantine_record``.
_KNOWN_TAGS_BY_KIND: Final[dict[str, frozenset[str]]] = {
    "INDI": KNOWN_INDI_TAGS,
    "FAM": KNOWN_FAM_TAGS,
    "SOUR": KNOWN_SOUR_TAGS,
    "NOTE": KNOWN_NOTE_TAGS,
    "OBJE": KNOWN_OBJE_TAGS,
    "REPO": KNOWN_REPO_TAGS,
    "SUBM": KNOWN_SUBM_TAGS,
    "HEAD": KNOWN_HEAD_TAGS,
}

#: Mapping top-level tag → owner_kind label для ``RawTagBlock.owner_kind``.
_OWNER_KIND_BY_TAG: Final[dict[str, str]] = {
    "INDI": "individual",
    "FAM": "family",
    "SOUR": "source",
    "NOTE": "note",
    "OBJE": "object",
    "REPO": "repository",
    "SUBM": "submitter",
    "HEAD": "header",
}


def quarantine_record(record: GedcomRecord) -> tuple[RawTagBlock, ...]:
    """Вытащить все unknown direct-children верхнеуровневой записи.

    Args:
        record: Top-level ``GedcomRecord`` (один из INDI/FAM/SOUR/NOTE/
            OBJE/REPO/SUBM/HEAD). Записи без xref'а (TRLR, второй HEAD)
            вызывающий должен фильтровать сам.

    Returns:
        Tuple of :class:`RawTagBlock` — по одному на каждого unknown
        direct-child'а. Если record'а тип неизвестен (custom 0-level
        tag), возвращается **весь record** одним блоком с path="" — это
        пограничный случай (например, ``0 @P1@ _PROP`` от какой-нибудь
        самописной утилиты), и сохранение целиком — самое безопасное.

    Notes:
        * Поддеревья known direct-children'ов на этой итерации НЕ
          сканируются — quarantine ограничен 1 уровнем по умолчанию.
          Это ADR-ed решение (Phase 5.5b расширит per-event).
        * ``path`` всегда ``""`` в текущей реализации.
    """
    tag = record.tag
    owner_xref_id = record.xref_id
    if owner_xref_id is None:
        # HEAD не имеет xref'а — используем свой собственный тег как label.
        # Это единственный «бесxref'ный» record, который мы quarantine'им.
        if tag != "HEAD":
            return ()
        owner_xref_id = "HEAD"

    owner_kind = _OWNER_KIND_BY_TAG.get(tag)
    if owner_kind is None:
        # Custom 0-level tag (нестандартный record-type) — сохраняем
        # запись целиком, чтобы export builder мог её эмитнуть как есть.
        return (
            RawTagBlock(
                owner_xref_id=owner_xref_id,
                owner_kind="custom",
                path="",
                record=record,
            ),
        )

    known = _KNOWN_TAGS_BY_KIND[tag]
    blocks: list[RawTagBlock] = []
    for child in record.children:
        if child.tag in known:
            continue
        blocks.append(
            RawTagBlock(
                owner_xref_id=owner_xref_id,
                owner_kind=owner_kind,
                path="",
                record=child,
            )
        )
    return tuple(blocks)


def quarantine_document(records: list[GedcomRecord]) -> tuple[RawTagBlock, ...]:
    """Quarantine все unknown direct-children всех top-level records.

    Args:
        records: Плоский список корневых ``GedcomRecord`` (вывод
            :func:`gedcom_parser.parser.parse_records`). TRLR
            пропускается; повторные HEAD на этой итерации тоже —
            quarantine только из первого HEAD'а (зеркалит
            ``GedcomDocument.from_records``).

    Returns:
        Tuple of all collected blocks across the document, в порядке
        обнаружения.
    """
    head_seen = False
    out: list[RawTagBlock] = []
    for record in records:
        tag = record.tag
        if tag == "TRLR":
            continue
        if tag == "HEAD":
            if head_seen:
                continue
            head_seen = True
            out.extend(quarantine_record(record))
            continue
        # Records требующие xref. Без xref'а семантический слой их
        # пропускает; quarantine — тоже (нет owner'а для блока).
        if record.xref_id is None:
            continue
        out.extend(quarantine_record(record))
    return tuple(out)


__all__ = [
    "KNOWN_FAM_TAGS",
    "KNOWN_HEAD_TAGS",
    "KNOWN_INDI_TAGS",
    "KNOWN_NOTE_TAGS",
    "KNOWN_OBJE_TAGS",
    "KNOWN_REPO_TAGS",
    "KNOWN_SOUR_TAGS",
    "KNOWN_SUBM_TAGS",
    "quarantine_document",
    "quarantine_record",
]
