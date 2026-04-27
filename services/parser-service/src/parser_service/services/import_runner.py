"""Импорт GEDCOM-файла в БД через bulk INSERT.

Перенесён из ``scripts/import_personal_ged.py`` как переиспользуемая функция.
Принимает уже распарсенный путь к ``.ged`` (CLI или upload), запускает парсер
и заливает persons / names / families / family_children / events /
event_participants.

Audit-режим: bulk-insert без построчных entries; один summary-entry уровня
import_job в конце. Это match'ит CLI-скрипт (см. ROADMAP §6.4 bench).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import warnings
from pathlib import Path
from typing import Any

from shared_models import set_audit_skip
from shared_models.enums import (
    ActorKind,
    AuditAction,
    DateCalendar,
    DateQualifier,
    EntityStatus,
    EventType,
    ImportJobStatus,
    ImportSourceKind,
    NameType,
    Sex,
    SourceType,
)
from shared_models.orm import (
    AuditLog,
    Citation,
    EntityMultimedia,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    ImportJob,
    MultimediaObject,
    Name,
    Person,
    Place,
    Source,
    Tree,
    User,
)
from shared_models.types import new_uuid
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

_BATCH_SIZE = 5000


def _sha256(path: Path) -> str:
    """SHA-256 файла для идемпотентности импорта."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _map_sex(value: str | None) -> str:
    """GEDCOM SEX → enum Sex."""
    if value == "M":
        return str(Sex.MALE.value)
    if value == "F":
        return str(Sex.FEMALE.value)
    if value == "X":
        return str(Sex.OTHER.value)
    return str(Sex.UNKNOWN.value)


# Множество тегов, для которых в shared_models.enums.EventType есть прямой
# элемент. Всё остальное (BLES, FCOM, EVEN, OCCU-подобные атрибуты, MARB и т.п.)
# раскладываем в EventType.CUSTOM с сохранением исходного тега в custom_type.
_KNOWN_EVENT_TAGS: frozenset[str] = frozenset(
    e.value for e in EventType if e is not EventType.CUSTOM
)


def _map_event_type(tag: str) -> tuple[str, str | None]:
    """GEDCOM event tag → (event_type, custom_type).

    Если тег есть в EventType — используем как есть (custom_type=None).
    Иначе — CUSTOM + оригинальный тег в custom_type (соблюдается CHECK-constraint
    custom_type_required_for_custom).
    """
    if tag in _KNOWN_EVENT_TAGS:
        return tag, None
    return EventType.CUSTOM.value, tag


# Маппинг qualifier'а парсера (gedcom_parser.dates.Qualifier) в DateQualifier.
# "INT" не имеет соответствия в нашем enum — фолбэк на None (дата осталась
# в date_raw, разбор в phrase у парсера, но мы пока phrase не персистим).
_QUALIFIER_MAP: dict[str, str] = {
    "ABT": DateQualifier.ABOUT.value,
    "CAL": DateQualifier.CALCULATED.value,
    "EST": DateQualifier.ESTIMATED.value,
    "BEF": DateQualifier.BEFORE.value,
    "AFT": DateQualifier.AFTER.value,
}


def _map_date_qualifier(qualifier: str, *, is_period: bool, is_range: bool) -> str | None:
    """ParsedDate.qualifier (+ is_period/is_range) → DateQualifier."""
    if is_period:
        return str(DateQualifier.FROM_TO.value)
    if is_range:
        return str(DateQualifier.BETWEEN.value)
    if qualifier == "none":
        return str(DateQualifier.EXACT.value)
    return _QUALIFIER_MAP.get(qualifier)


# Календарь парсера → DateCalendar (где есть соответствие).
_CALENDAR_MAP: dict[str, str] = {
    "gregorian": DateCalendar.GREGORIAN.value,
    "julian": DateCalendar.JULIAN.value,
    "hebrew": DateCalendar.HEBREW.value,
    "french-r": DateCalendar.FRENCH_REPUBLICAN.value,
}


def _map_date_calendar(calendar: str) -> str | None:
    """ParsedDate.calendar → DateCalendar (roman/unknown → None)."""
    return _CALENDAR_MAP.get(calendar)


# GEDCOM QUAY (quality of evidence): 0 = unreliable, 3 = primary evidence.
# В нашем `Citation.quality` (Float, default 0.5) семантика — нормализованный
# 0..1 score, поэтому делим QUAY на 3. Невалидный/отсутствующий QUAY → None
# (вызывающий ставит дефолт ORM — 0.5).
_QUAY_MAX = 3


def _quay_to_quality(value: str | None) -> float | None:
    """GEDCOM QUAY ('0'..'3') → Citation.quality (0.0..1.0). None если не задан."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        quay = int(stripped)
    except ValueError:
        return None
    if quay < 0 or quay > _QUAY_MAX:
        return None
    return quay / _QUAY_MAX


def _chunk(seq: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Разбить список dict'ов на чанки фиксированного размера."""
    return [seq[i : i + size] for i in range(0, len(seq), size)]


async def _bulk_insert(session: AsyncSession, model: Any, rows: list[dict[str, Any]]) -> None:
    """Bulk INSERT с разбивкой на батчи."""
    if not rows:
        return
    for chunk in _chunk(rows, _BATCH_SIZE):
        await session.execute(insert(model), chunk)


async def _ensure_owner(session: AsyncSession, email: str) -> User:
    """Найти существующего user по email или создать нового."""
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        external_auth_id=f"local:{email}",
        display_name=email.split("@", maxsplit=1)[0],
        locale="en",
    )
    session.add(user)
    await session.flush()
    return user


async def run_import(
    session: AsyncSession,
    ged_path: Path,
    *,
    owner_email: str,
    tree_name: str | None = None,
    source_filename: str | None = None,
) -> ImportJob:
    """Распарсить GEDCOM-файл и записать в БД.

    Args:
        session: Активная async-сессия (commit/rollback — на caller).
        ged_path: Локальный путь к .ged файлу.
        owner_email: Email user'а-владельца дерева. Создаётся, если нет.
        tree_name: Имя нового дерева. По умолчанию — basename файла.
        source_filename: Оригинальное имя файла (для upload-сценария, когда
            ``ged_path`` указывает на временный файл). По умолчанию —
            ``ged_path.name``.

    Returns:
        Созданный ``ImportJob`` со статусом ``succeeded`` и заполненными stats.

    Raises:
        FileNotFoundError: Если файл не найден.
        Exception: Любая ошибка парсера или БД — пробрасывается выше; вызывающий
            код должен пометить job.status = "failed" и сохранить ошибку в reason.
    """
    if not ged_path.exists():
        msg = f"GEDCOM file not found: {ged_path}"
        raise FileNotFoundError(msg)

    # Audit-listener регистрируется глобально в database.init_engine().
    sha = _sha256(ged_path)
    display_filename = source_filename or ged_path.name
    owner = await _ensure_owner(session, owner_email)

    # Tree использует TreeOwnedMixins (без StatusMixin) — не передаём status/confidence.
    tree = Tree(
        owner_user_id=owner.id,
        name=tree_name or ged_path.stem,
        visibility="private",
        default_locale="en",
        settings={},
        provenance={"source_filename": display_filename, "source_sha256": sha},
        version_id=1,
    )
    session.add(tree)
    await session.flush()

    job = ImportJob(
        tree_id=tree.id,
        created_by_user_id=owner.id,
        source_kind=ImportSourceKind.GEDCOM.value,
        source_filename=display_filename,
        source_sha256=sha,
        status=ImportJobStatus.RUNNING.value,
        started_at=dt.datetime.now(dt.UTC),
    )
    session.add(job)
    await session.flush()

    # Парсинг GEDCOM. Используем parse_file (а не parse_document_file), чтобы
    # получить и raw `records`, и encoding. Records нужны для извлечения
    # SOUR-references с подтегами PAGE/QUAY (см. citations ниже): семантические
    # entity'ы из `document` хранят только tuple xref'ов, без подробностей.
    from gedcom_parser import GedcomDocument, parse_file

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        records, encoding = parse_file(ged_path)
        document = GedcomDocument.from_records(records, encoding=encoding)

    set_audit_skip(session.sync_session, True)
    try:
        # ---- Persons ----
        person_rows: list[dict[str, Any]] = []
        person_id_by_xref: dict[str, Any] = {}
        now = dt.datetime.now(dt.UTC)
        for xref, person in document.persons.items():
            pid = new_uuid()
            person_id_by_xref[xref] = pid
            person_rows.append(
                {
                    "id": pid,
                    "tree_id": tree.id,
                    "gedcom_xref": xref,
                    "sex": _map_sex(person.sex),
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": {"import_job_id": str(job.id)},
                    "created_at": now,
                    "updated_at": now,
                }
            )
        await _bulk_insert(session, Person, person_rows)

        # ---- Names ----
        name_rows: list[dict[str, Any]] = []
        for xref, person in document.persons.items():
            person_id = person_id_by_xref[xref]
            for sort_order, name in enumerate(person.names):
                name_rows.append(
                    {
                        "id": new_uuid(),
                        "person_id": person_id,
                        "given_name": name.given,
                        "surname": name.surname,
                        "sort_order": sort_order,
                        "name_type": (
                            NameType.BIRTH.value if sort_order == 0 else NameType.AKA.value
                        ),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
        await _bulk_insert(session, Name, name_rows)

        # ---- Families + family_children ----
        family_rows: list[dict[str, Any]] = []
        family_id_by_xref: dict[str, Any] = {}
        for xref, family in document.families.items():
            fid = new_uuid()
            family_id_by_xref[xref] = fid
            family_rows.append(
                {
                    "id": fid,
                    "tree_id": tree.id,
                    "gedcom_xref": xref,
                    "husband_id": person_id_by_xref.get(family.husband_xref or ""),
                    "wife_id": person_id_by_xref.get(family.wife_xref or ""),
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": {"import_job_id": str(job.id)},
                    "created_at": now,
                    "updated_at": now,
                }
            )
        await _bulk_insert(session, Family, family_rows)

        fc_rows: list[dict[str, Any]] = []
        for xref, family in document.families.items():
            fid = family_id_by_xref[xref]
            for child_xref in family.children_xrefs:
                child_id = person_id_by_xref.get(child_xref)
                if child_id is None:
                    continue
                fc_rows.append(
                    {
                        "id": new_uuid(),
                        "family_id": fid,
                        "child_person_id": child_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
        await _bulk_insert(session, FamilyChild, fc_rows)

        # ---- Places (dedup по raw text в пределах дерева) ----
        # Собираем уникальные PLAC-строки из всех событий и инсёртим один раз.
        # Полную канонизацию (PlaceAlias, исторические границы, geocoding)
        # оставили на Phase 3.4+ — здесь только raw → places.canonical_name.
        place_rows: list[dict[str, Any]] = []
        place_id_by_raw: dict[str, Any] = {}

        def _register_place(raw: str | None) -> None:
            """Зарегистрировать уникальный PLAC raw для bulk insert."""
            if not raw:
                return
            key = raw.strip()
            if not key or key in place_id_by_raw:
                return
            pid = new_uuid()
            place_id_by_raw[key] = pid
            place_rows.append(
                {
                    "id": pid,
                    "tree_id": tree.id,
                    "canonical_name": key,
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": {"import_job_id": str(job.id)},
                    "created_at": now,
                    "updated_at": now,
                }
            )

        for person in document.persons.values():
            for ev in person.events:
                _register_place(ev.place_raw)
        for family in document.families.values():
            for ev in family.events:
                _register_place(ev.place_raw)

        await _bulk_insert(session, Place, place_rows)

        # ---- Sources ----
        # SOUR-записи документа → bulk insert в `sources`. Поля:
        #  - title:       обязательно (NOT NULL); если у GEDCOM-источника TITL
        #                 пуст — фолбэк на xref, чтобы соблюсти constraint.
        #  - author/publication: TITL.AUTH/PUBL (опционально).
        #  - source_type: пока всегда OTHER. Классификация (book/metric_record/...) —
        #                 Phase 3.4 (entity resolution).
        #  - repository:  free-form name; xref на `repositories` пока не разворачиваем
        #                 (REPO импортируем в отдельной фазе).
        # Дедупликация по title — Phase 3.4 (см. ROADMAP).
        source_rows: list[dict[str, Any]] = []
        source_id_by_xref: dict[str, Any] = {}
        for xref, parsed_source in document.sources.items():
            sid = new_uuid()
            source_id_by_xref[xref] = sid
            source_rows.append(
                {
                    "id": sid,
                    "tree_id": tree.id,
                    "title": parsed_source.title or xref,
                    "author": parsed_source.author,
                    "publication": parsed_source.publication,
                    "source_type": SourceType.OTHER.value,
                    "repository": None,
                    "repository_id": None,
                    "url": None,
                    "publication_date": None,
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": {"import_job_id": str(job.id), "gedcom_xref": xref},
                    "created_at": now,
                    "updated_at": now,
                }
            )
        await _bulk_insert(session, Source, source_rows)

        # ---- Events + EventParticipants ----
        # Persona events → один participant с role="principal".
        # FAM events → оба супруга participants (role="husband"/"wife"), если
        # они указаны в FAM. Если нет ни одного супруга (редкий случай —
        # broken GEDCOM), оставляем family-level participant как fallback,
        # чтобы соблюсти CHECK (person_id OR family_id).
        event_rows: list[dict[str, Any]] = []
        participant_rows: list[dict[str, Any]] = []
        # Маппинг GEDCOM line_no события → event_id. Нужен Task 2 (citations),
        # чтобы по raw GedcomRecord события найти соответствующий event_id
        # без изменения парсера. line_no уникален в пределах файла.
        event_id_by_line_no: dict[int, Any] = {}

        def _resolve_place_id(raw: str | None) -> Any | None:
            """Найти place_id по PLAC raw (после strip), либо None."""
            if not raw:
                return None
            return place_id_by_raw.get(raw.strip())

        def _append_event(ev: Any) -> Any:
            """Собрать одну запись Event и вернуть её id."""
            event_id = new_uuid()
            if ev.line_no is not None:
                event_id_by_line_no[ev.line_no] = event_id
            event_type, custom_type = _map_event_type(ev.tag)

            date_start = None
            date_end = None
            date_qualifier = None
            date_calendar = None
            if ev.date is not None:
                date_start = ev.date.date_lower
                date_end = ev.date.date_upper
                date_qualifier = _map_date_qualifier(
                    ev.date.qualifier,
                    is_period=ev.date.is_period,
                    is_range=ev.date.is_range,
                )
                date_calendar = _map_date_calendar(ev.date.calendar)

            event_rows.append(
                {
                    "id": event_id,
                    "tree_id": tree.id,
                    "event_type": event_type,
                    "custom_type": custom_type,
                    "place_id": _resolve_place_id(ev.place_raw),
                    "date_raw": ev.date_raw,
                    "date_start": date_start,
                    "date_end": date_end,
                    "date_qualifier": date_qualifier,
                    "date_calendar": date_calendar,
                    "description": None,
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": {"import_job_id": str(job.id)},
                    "created_at": now,
                    "updated_at": now,
                }
            )
            return event_id

        def _append_participant(
            *,
            event_id: Any,
            person_id: Any | None = None,
            family_id: Any | None = None,
            role: str,
        ) -> None:
            """Зарегистрировать одну строку event_participants."""
            participant_rows.append(
                {
                    "id": new_uuid(),
                    "event_id": event_id,
                    "person_id": person_id,
                    "family_id": family_id,
                    "role": role,
                    "created_at": now,
                    "updated_at": now,
                }
            )

        for xref, person in document.persons.items():
            person_pk = person_id_by_xref[xref]
            for ev in person.events:
                eid = _append_event(ev)
                _append_participant(event_id=eid, person_id=person_pk, role="principal")

        for xref, family in document.families.items():
            family_pk = family_id_by_xref[xref]
            husband_pk = person_id_by_xref.get(family.husband_xref or "")
            wife_pk = person_id_by_xref.get(family.wife_xref or "")
            for ev in family.events:
                eid = _append_event(ev)
                if husband_pk is not None:
                    _append_participant(event_id=eid, person_id=husband_pk, role="husband")
                if wife_pk is not None:
                    _append_participant(event_id=eid, person_id=wife_pk, role="wife")
                if husband_pk is None and wife_pk is None:
                    # Fallback: FAM без обоих супругов — привязываем семью.
                    _append_participant(event_id=eid, family_id=family_pk, role="principal")

        await _bulk_insert(session, Event, event_rows)
        await _bulk_insert(session, EventParticipant, participant_rows)

        # ---- Citations ----
        # Собираем SOUR-references из raw GedcomRecord. Семантический слой
        # gedcom_parser хранит только tuple xref'ов — без подтегов PAGE/QUAY,
        # поэтому идём по сырым записям.
        # Citation.entity_type ∈ {"person", "family", "event"}; для каждого
        # SOUR с xref-значением создаём одну строку.
        citation_rows: list[dict[str, Any]] = []

        def _collect_citations(
            *,
            owner_record: Any,
            entity_type: str,
            entity_id: Any,
        ) -> None:
            """Найти SOUR-children у `owner_record` и записать citations."""
            for sour in owner_record.find_all("SOUR"):
                value = sour.value
                if not (value.startswith("@") and value.endswith("@")):
                    # Inline-source (1 SOUR <text>) — без xref, в этой фазе
                    # игнорируем (Phase 3.4: создавать ad-hoc Source).
                    continue
                src_xref = value.strip("@")
                src_id = source_id_by_xref.get(src_xref)
                if src_id is None:
                    # Висячая ссылка — пропускаем тихо (verify_references
                    # отдельно эмитит warning).
                    continue
                quality = _quay_to_quality(sour.get_value("QUAY") or None)
                citation: dict[str, Any] = {
                    "id": new_uuid(),
                    "tree_id": tree.id,
                    "source_id": src_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "page_or_section": sour.get_value("PAGE") or None,
                    "quoted_text": None,
                    "note": None,
                    "provenance": {"import_job_id": str(job.id)},
                    "created_at": now,
                    "updated_at": now,
                }
                if quality is not None:
                    citation["quality"] = quality
                citation_rows.append(citation)

        # Идентифицируем event-children по line_no: если line_no попал
        # в `event_id_by_line_no`, мы его реально вставили как Event.
        # Это покрывает и custom-теги (CUSTOM event_type), без отдельного списка.
        for root in records:
            if root.xref_id is None:
                continue
            if root.tag == "INDI":
                owner_pk = person_id_by_xref.get(root.xref_id)
                owner_kind = "person"
            elif root.tag == "FAM":
                owner_pk = family_id_by_xref.get(root.xref_id)
                owner_kind = "family"
            else:
                continue
            if owner_pk is not None:
                _collect_citations(
                    owner_record=root,
                    entity_type=owner_kind,
                    entity_id=owner_pk,
                )
            for child in root.children:
                event_pk = event_id_by_line_no.get(child.line_no)
                if event_pk is None:
                    continue
                _collect_citations(
                    owner_record=child,
                    entity_type="event",
                    entity_id=event_pk,
                )

        await _bulk_insert(session, Citation, citation_rows)

        # ---- Multimedia (OBJE) ----
        # Top-level OBJE-records → `multimedia_objects` + ссылки из INDI/FAM
        # (`1 OBJE @M1@`) → `entity_multimedia`. Phase 3.5 follow-up: inline
        # OBJE (`1 OBJE\n2 FILE foo.jpg`) тоже теперь сохраняются — каждый
        # как отдельный multimedia_objects row + entity_multimedia link.
        # Бинарные данные НЕ скачиваем — только метаданные:
        #  - storage_url:  значение тега FILE (relative или absolute path/URL).
        #                  Поле NOT NULL: пустые/отсутствующие FILE → фолбэк
        #                  на `gedcom://OBJE/<xref>` (top-level) или
        #                  `gedcom://OBJE/inline/<line_no>` (inline).
        #  - object_type:  фолбэк "image" — gedcom_parser не классифицирует
        #                  тип; FORM/TYPE-расширение хранится в metadata.
        #  - mime_type:    None (выводить из FORM — Phase 3.5.1).
        #  - sha256:       None (мы не открываем файл).
        #  - caption:      OBJE.TITL.
        #  - object_metadata: {format, type, gedcom_xref|inline_owner_xref,
        #                  created_raw} — всё, что относится к raw GEDCOM, без потерь.
        multimedia_rows: list[dict[str, Any]] = []
        multimedia_id_by_xref: dict[str, Any] = {}

        def _build_object_metadata(
            *,
            obje: Any,
            owner_kind: str,
            owner_ref: str,
        ) -> dict[str, Any]:
            """Собрать ``object_metadata`` для одного OBJE row.

            owner_kind: ``"top"`` (top-level OBJE с xref) или ``"inline"``
                (inline OBJE на INDI/FAM/SOUR).
            owner_ref: gedcom_xref top-level OBJE, либо xref INDI/FAM-владельца
                (для inline формы).
            """
            md: dict[str, Any] = {}
            if owner_kind == "top":
                md["gedcom_xref"] = owner_ref
            else:
                md["inline"] = True
                md["inline_owner_xref"] = owner_ref
            if obje.format_:
                md["format"] = obje.format_
            type_ = getattr(obje, "type_", None)
            if type_:
                md["type"] = type_
            created_raw = getattr(obje, "created_raw", None)
            if created_raw:
                md["created_raw"] = created_raw
            return md

        # Top-level OBJE (с xref).
        for xref, obj in document.objects.items():
            mid = new_uuid()
            multimedia_id_by_xref[xref] = mid
            md = _build_object_metadata(obje=obj, owner_kind="top", owner_ref=xref)
            prov: dict[str, Any] = {"import_job_id": str(job.id), "gedcom_xref": xref}
            if obj.created_raw:
                prov["gedcom_crea"] = obj.created_raw
            multimedia_rows.append(
                {
                    "id": mid,
                    "tree_id": tree.id,
                    "object_type": "image",
                    "storage_url": obj.file or f"gedcom://OBJE/{xref}",
                    "mime_type": None,
                    "size_bytes": None,
                    "sha256": None,
                    "caption": obj.title,
                    "taken_date": None,
                    "object_metadata": md,
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": prov,
                    "created_at": now,
                    "updated_at": now,
                }
            )

        # OBJE-references + inline OBJE → entity_multimedia (полиморфно).
        entity_multimedia_rows: list[dict[str, Any]] = []

        def _add_media_links(
            *,
            owner_xref_iter: Any,
            entity_type: str,
            entity_id: Any,
        ) -> None:
            """Для каждого xref-объекта в owner_xref_iter — добавить
            entity_multimedia link на уже созданный top-level multimedia row."""
            for obj_xref in owner_xref_iter:
                mid = multimedia_id_by_xref.get(obj_xref)
                if mid is None:
                    continue
                entity_multimedia_rows.append(
                    {
                        "id": new_uuid(),
                        "multimedia_id": mid,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "role": "primary",
                        "created_at": now,
                        "updated_at": now,
                    }
                )

        def _add_inline_objects(
            *,
            owner_xref: str,
            inline_iter: Any,
            entity_type: str,
            entity_id: Any,
        ) -> None:
            """Для каждого inline-OBJE — создать multimedia_objects row + link.

            Inline OBJE не имеет gedcom_xref — мы кодируем владельца в
            ``object_metadata.inline_owner_xref`` для traceability.
            """
            for inline in inline_iter:
                mid = new_uuid()
                line_no = inline.line_no or 0
                md = _build_object_metadata(
                    obje=inline,
                    owner_kind="inline",
                    owner_ref=owner_xref,
                )
                fallback = f"gedcom://OBJE/inline/{owner_xref}/{line_no}"
                multimedia_rows.append(
                    {
                        "id": mid,
                        "tree_id": tree.id,
                        "object_type": "image",
                        "storage_url": inline.file or fallback,
                        "mime_type": None,
                        "size_bytes": None,
                        "sha256": None,
                        "caption": inline.title,
                        "taken_date": None,
                        "object_metadata": md,
                        "status": EntityStatus.PROBABLE.value,
                        "confidence_score": 0.5,
                        "version_id": 1,
                        "provenance": {
                            "import_job_id": str(job.id),
                            "inline": True,
                            "inline_owner_xref": owner_xref,
                            "inline_line_no": line_no,
                        },
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                entity_multimedia_rows.append(
                    {
                        "id": new_uuid(),
                        "multimedia_id": mid,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "role": "primary",
                        "created_at": now,
                        "updated_at": now,
                    }
                )

        for xref, person in document.persons.items():
            person_pk = person_id_by_xref.get(xref)
            if person_pk is None:
                continue
            _add_media_links(
                owner_xref_iter=person.objects_xrefs,
                entity_type="person",
                entity_id=person_pk,
            )
            _add_inline_objects(
                owner_xref=xref,
                inline_iter=person.inline_objects,
                entity_type="person",
                entity_id=person_pk,
            )
        for xref, family in document.families.items():
            family_pk = family_id_by_xref.get(xref)
            if family_pk is None:
                continue
            _add_media_links(
                owner_xref_iter=family.objects_xrefs,
                entity_type="family",
                entity_id=family_pk,
            )
            _add_inline_objects(
                owner_xref=xref,
                inline_iter=family.inline_objects,
                entity_type="family",
                entity_id=family_pk,
            )

        await _bulk_insert(session, MultimediaObject, multimedia_rows)
        await _bulk_insert(session, EntityMultimedia, entity_multimedia_rows)

        stats = {
            "persons": len(person_rows),
            "names": len(name_rows),
            "families": len(family_rows),
            "family_children": len(fc_rows),
            "places": len(place_rows),
            "sources": len(source_rows),
            "events": len(event_rows),
            "event_participants": len(participant_rows),
            "citations": len(citation_rows),
            "multimedia": len(multimedia_rows),
            "entity_multimedia": len(entity_multimedia_rows),
        }
    finally:
        set_audit_skip(session.sync_session, False)

    job.status = ImportJobStatus.SUCCEEDED.value
    job.stats = stats
    job.finished_at = dt.datetime.now(dt.UTC)

    session.add(
        AuditLog(
            tree_id=tree.id,
            entity_type="import_jobs",
            entity_id=job.id,
            action=AuditAction.INSERT.value,
            actor_user_id=owner.id,
            actor_kind=ActorKind.IMPORT_JOB.value,
            import_job_id=job.id,
            reason=f"API import of {display_filename}",
            diff={"summary": stats, "source_sha256": sha, "fields": list(stats.keys())},
        )
    )
    return job
