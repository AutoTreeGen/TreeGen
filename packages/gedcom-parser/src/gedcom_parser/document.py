"""Документ GEDCOM: индексированный набор сущностей + проверка ссылок.

Поверх AST (:class:`gedcom_parser.models.GedcomRecord`) собирается
:class:`GedcomDocument` — единый объект с индексами по xref для всех
типов записей. Это то, на что опираются writer, валидатор, маппер в БД.

В этой итерации семантический слой read-only: ``GedcomDocument`` строится
один раз через :meth:`GedcomDocument.from_records` и не мутируется.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from gedcom_parser.entities import (
    Event,
    Family,
    Header,
    MultimediaObject,
    Note,
    Person,
    Repository,
    Source,
    Submitter,
)
from gedcom_parser.exceptions import GedcomReferenceWarning

# EncodingInfo используется как тип Pydantic-поля и должен быть доступен в
# runtime при разрешении model fields — поэтому импорт обычный, не TYPE_CHECKING.
from gedcom_parser.models import EncodingInfo  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Container

    from gedcom_parser.models import GedcomRecord


class BrokenRef(BaseModel):
    """Описание висячей xref-ссылки.

    Возвращается списком из :meth:`GedcomDocument.verify_references` —
    параллельно с эмитом :class:`GedcomReferenceWarning`. Программный
    список удобен в CLI и тестах; warning — для общего лога.

    Атрибуты:
        owner_xref: xref сущности, в которой обнаружена битая ссылка.
        owner_kind: Тип сущности-владельца (``"person"``, ``"family"`` и т.д.).
        field: Имя поля или GEDCOM-тега, по которому шла ссылка
            (например, ``"husband_xref"`` или ``"FAMS"``).
        target_xref: xref, на который ссылались, но который не нашли в индексе.
        expected_kind: Ожидаемый тип цели (``"family"`` для FAMS, ``"person"``
            для HUSB/WIFE/CHIL, и т.д.).
    """

    owner_xref: str
    owner_kind: str
    field: str
    target_xref: str
    expected_kind: str

    model_config = ConfigDict(frozen=True)


class GedcomDocument(BaseModel):
    """Семантическое представление одного GEDCOM-файла.

    Все верхнеуровневые записи разложены по индексам ``xref_id → entity``.
    Inline-сущности (NOTE без xref, embedded MULTI и т.д.) живут внутри
    своих владельцев и в индексах не появляются.

    Метод :meth:`from_records` собирает документ из списка корневых
    ``GedcomRecord`` (вывод :func:`gedcom_parser.parser.parse_records`).
    """

    header: Header | None = None
    persons: dict[str, Person] = Field(default_factory=dict)
    families: dict[str, Family] = Field(default_factory=dict)
    sources: dict[str, Source] = Field(default_factory=dict)
    notes: dict[str, Note] = Field(default_factory=dict)
    objects: dict[str, MultimediaObject] = Field(default_factory=dict)
    repositories: dict[str, Repository] = Field(default_factory=dict)
    submitters: dict[str, Submitter] = Field(default_factory=dict)
    encoding: EncodingInfo | None = None

    # Документ — мутабельный контейнер: его ещё могут наполнять или фильтровать
    # снаружи. Сами сущности внутри — frozen.
    model_config = ConfigDict(frozen=False, extra="forbid", arbitrary_types_allowed=False)

    # ----- Конструктор ----------------------------------------------------

    @classmethod
    def from_records(
        cls,
        records: list[GedcomRecord],
        *,
        encoding: EncodingInfo | None = None,
    ) -> GedcomDocument:
        """Собрать документ из плоского списка корневых записей.

        Корни без xref'а (HEAD, TRLR, потенциально вторая HEAD из битого
        мерж-файла) обрабатываются специально:

        * Первый ``HEAD`` записывается в ``header``; повторные игнорируются.
        * ``TRLR`` пропускается (служебный маркер конца файла).
        * Запись с известным тегом, но без xref — пропускается (нечего
          класть в индекс). Запись с неизвестным тегом — также пропускается:
          валидатор (отдельная подзадача) разберётся.
        """
        doc = cls(encoding=encoding)

        for record in records:
            tag = record.tag

            if tag == "HEAD":
                if doc.header is None:
                    doc.header = Header.from_record(record)
                continue
            if tag == "TRLR":
                continue

            # Дальше — записи с обязательным xref. Без него класть некуда.
            if record.xref_id is None:
                continue

            if tag == "INDI":
                doc.persons[record.xref_id] = Person.from_record(record)
            elif tag == "FAM":
                doc.families[record.xref_id] = Family.from_record(record)
            elif tag == "SOUR":
                doc.sources[record.xref_id] = Source.from_record(record)
            elif tag == "NOTE":
                doc.notes[record.xref_id] = Note.from_record(record)
            elif tag == "OBJE":
                doc.objects[record.xref_id] = MultimediaObject.from_record(record)
            elif tag == "REPO":
                doc.repositories[record.xref_id] = Repository.from_record(record)
            elif tag == "SUBM":
                doc.submitters[record.xref_id] = Submitter.from_record(record)
            # Прочие теги верхнего уровня (проприетарные расширения и т.д.)
            # сохраняем в семантику только когда явно понадобятся.

        return doc

    # ----- Удобные аксессоры ---------------------------------------------

    def get_person(self, xref_id: str) -> Person | None:
        """Персона по xref или ``None``."""
        return self.persons.get(xref_id)

    def get_family(self, xref_id: str) -> Family | None:
        """Семья по xref или ``None``."""
        return self.families.get(xref_id)

    # ----- Проверка ссылок ------------------------------------------------

    def verify_references(self, *, warn: bool = True) -> list[BrokenRef]:
        """Найти все висячие xref-ссылки в документе.

        Проверяются связи:

        * ``Person.families_as_spouse`` / ``families_as_child`` → ``families``
        * ``Person.notes_xrefs`` / ``Family.notes_xrefs`` / у событий → ``notes``
        * ``Person.sources_xrefs`` / ``Family.sources_xrefs`` / у событий → ``sources``
        * ``Person.objects_xrefs`` / ``Family.objects_xrefs`` → ``objects``
        * ``Family.husband_xref`` / ``wife_xref`` / ``children_xrefs`` → ``persons``
        * ``Source.repository_xref`` → ``repositories``
        * ``Header.submitter_xref`` → ``submitters``

        Args:
            warn: Если ``True`` (по умолчанию), для каждой битой ссылки
                эмитируется :class:`GedcomReferenceWarning`. ``False`` —
                «тихий» режим (только возврат списка).

        Returns:
            Список :class:`BrokenRef` — по одному на каждую висячую ссылку,
            в порядке обнаружения.
        """
        broken: list[BrokenRef] = []
        broken.extend(self._verify_persons())
        broken.extend(self._verify_families())
        broken.extend(self._verify_sources())
        broken.extend(self._verify_header())

        if warn:
            for ref in broken:
                warnings.warn(
                    f"Dangling xref @{ref.target_xref}@ in {ref.owner_kind} "
                    f"{ref.owner_xref}/{ref.field}: no {ref.expected_kind} "
                    f"with this id",
                    GedcomReferenceWarning,
                    stacklevel=2,
                )

        return broken

    def _verify_persons(self) -> list[BrokenRef]:
        """Висячие ссылки в записях ``Person`` и их событиях."""
        out: list[BrokenRef] = []
        for person in self.persons.values():
            for fam_xref in person.families_as_spouse:
                if fam_xref not in self.families:
                    out.append(
                        BrokenRef(
                            owner_xref=person.xref_id,
                            owner_kind="person",
                            field="FAMS",
                            target_xref=fam_xref,
                            expected_kind="family",
                        )
                    )
            for fam_xref in person.families_as_child:
                if fam_xref not in self.families:
                    out.append(
                        BrokenRef(
                            owner_xref=person.xref_id,
                            owner_kind="person",
                            field="FAMC",
                            target_xref=fam_xref,
                            expected_kind="family",
                        )
                    )
            out.extend(
                self._check_xrefs(
                    owner_xref=person.xref_id,
                    owner_kind="person",
                    field="NOTE",
                    targets=person.notes_xrefs,
                    index=self.notes,
                    expected_kind="note",
                )
            )
            out.extend(
                self._check_xrefs(
                    owner_xref=person.xref_id,
                    owner_kind="person",
                    field="SOUR",
                    targets=person.sources_xrefs,
                    index=self.sources,
                    expected_kind="source",
                )
            )
            out.extend(
                self._check_xrefs(
                    owner_xref=person.xref_id,
                    owner_kind="person",
                    field="OBJE",
                    targets=person.objects_xrefs,
                    index=self.objects,
                    expected_kind="object",
                )
            )
            for event in person.events:
                out.extend(self._check_event(person.xref_id, "person", event))
        return out

    def _verify_families(self) -> list[BrokenRef]:
        """Висячие ссылки в записях ``Family`` и их событиях."""
        out: list[BrokenRef] = []
        for family in self.families.values():
            if family.husband_xref is not None and family.husband_xref not in self.persons:
                out.append(
                    BrokenRef(
                        owner_xref=family.xref_id,
                        owner_kind="family",
                        field="HUSB",
                        target_xref=family.husband_xref,
                        expected_kind="person",
                    )
                )
            if family.wife_xref is not None and family.wife_xref not in self.persons:
                out.append(
                    BrokenRef(
                        owner_xref=family.xref_id,
                        owner_kind="family",
                        field="WIFE",
                        target_xref=family.wife_xref,
                        expected_kind="person",
                    )
                )
            out.extend(
                self._check_xrefs(
                    owner_xref=family.xref_id,
                    owner_kind="family",
                    field="CHIL",
                    targets=family.children_xrefs,
                    index=self.persons,
                    expected_kind="person",
                )
            )
            out.extend(
                self._check_xrefs(
                    owner_xref=family.xref_id,
                    owner_kind="family",
                    field="NOTE",
                    targets=family.notes_xrefs,
                    index=self.notes,
                    expected_kind="note",
                )
            )
            out.extend(
                self._check_xrefs(
                    owner_xref=family.xref_id,
                    owner_kind="family",
                    field="SOUR",
                    targets=family.sources_xrefs,
                    index=self.sources,
                    expected_kind="source",
                )
            )
            out.extend(
                self._check_xrefs(
                    owner_xref=family.xref_id,
                    owner_kind="family",
                    field="OBJE",
                    targets=family.objects_xrefs,
                    index=self.objects,
                    expected_kind="object",
                )
            )
            for event in family.events:
                out.extend(self._check_event(family.xref_id, "family", event))
        return out

    def _verify_sources(self) -> list[BrokenRef]:
        """Висячие ссылки на репозитории в записях ``Source``."""
        out: list[BrokenRef] = []
        for source in self.sources.values():
            if (
                source.repository_xref is not None
                and source.repository_xref not in self.repositories
            ):
                out.append(
                    BrokenRef(
                        owner_xref=source.xref_id,
                        owner_kind="source",
                        field="REPO",
                        target_xref=source.repository_xref,
                        expected_kind="repository",
                    )
                )
        return out

    def _verify_header(self) -> list[BrokenRef]:
        """Висячая ссылка ``HEAD/SUBM`` на отправителя."""
        if (
            self.header is not None
            and self.header.submitter_xref is not None
            and self.header.submitter_xref not in self.submitters
        ):
            return [
                BrokenRef(
                    owner_xref="HEAD",
                    owner_kind="header",
                    field="SUBM",
                    target_xref=self.header.submitter_xref,
                    expected_kind="submitter",
                )
            ]
        return []

    # ----- Внутренние помощники ------------------------------------------

    @staticmethod
    def _check_xrefs(
        *,
        owner_xref: str,
        owner_kind: str,
        field: str,
        targets: tuple[str, ...],
        index: Container[str],
        expected_kind: str,
    ) -> list[BrokenRef]:
        """Проверить, что все xref'ы из ``targets`` есть в ``index``."""
        out: list[BrokenRef] = []
        for target in targets:
            if target not in index:
                out.append(
                    BrokenRef(
                        owner_xref=owner_xref,
                        owner_kind=owner_kind,
                        field=field,
                        target_xref=target,
                        expected_kind=expected_kind,
                    )
                )
        return out

    def _check_event(self, owner_xref: str, owner_kind: str, event: Event) -> list[BrokenRef]:
        """Проверить ссылки внутри события (NOTE/SOUR)."""
        out: list[BrokenRef] = []
        out.extend(
            self._check_xrefs(
                owner_xref=owner_xref,
                owner_kind=owner_kind,
                field=f"{event.tag}.NOTE",
                targets=event.notes_xrefs,
                index=self.notes,
                expected_kind="note",
            )
        )
        out.extend(
            self._check_xrefs(
                owner_xref=owner_xref,
                owner_kind=owner_kind,
                field=f"{event.tag}.SOUR",
                targets=event.sources_xrefs,
                index=self.sources,
                expected_kind="source",
            )
        )
        return out


__all__ = ["BrokenRef", "GedcomDocument"]
