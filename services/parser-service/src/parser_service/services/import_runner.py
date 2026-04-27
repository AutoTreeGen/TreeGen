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
)
from shared_models.orm import (
    AuditLog,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    ImportJob,
    Name,
    Person,
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

    # Парсинг GEDCOM
    from gedcom_parser import parse_document_file  # type: ignore[import-not-found]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        document = parse_document_file(ged_path)

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

        # ---- Events + EventParticipants ----
        # Каждое событие у персоны/семьи раскладывается в одну строку events
        # и одну строку event_participants (single principal). Multi-principal
        # события (например MARR с обоими супругами как participants) добавим
        # позже отдельно — Phase 3.2 (см. ROADMAP §7).
        event_rows: list[dict[str, Any]] = []
        participant_rows: list[dict[str, Any]] = []

        def _append_event(
            *,
            owner_kind: str,
            owner_id: Any,
            ev: Any,
        ) -> None:
            """Собрать одну запись Event + один EventParticipant."""
            event_id = new_uuid()
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
                    "place_id": None,  # Place-импорт — Phase 3.2.
                    "date_raw": ev.date_raw,
                    "date_start": date_start,
                    "date_end": date_end,
                    "date_qualifier": date_qualifier,
                    "date_calendar": date_calendar,
                    "description": None,  # PLAC raw сохраним при Place-импорте — Phase 3.2.
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": {"import_job_id": str(job.id)},
                    "created_at": now,
                    "updated_at": now,
                }
            )
            participant_rows.append(
                {
                    "id": new_uuid(),
                    "event_id": event_id,
                    "person_id": owner_id if owner_kind == "person" else None,
                    "family_id": owner_id if owner_kind == "family" else None,
                    "role": "principal",
                    "created_at": now,
                    "updated_at": now,
                }
            )

        for xref, person in document.persons.items():
            owner_id = person_id_by_xref[xref]
            for ev in person.events:
                _append_event(owner_kind="person", owner_id=owner_id, ev=ev)

        for xref, family in document.families.items():
            owner_id = family_id_by_xref[xref]
            for ev in family.events:
                _append_event(owner_kind="family", owner_id=owner_id, ev=ev)

        await _bulk_insert(session, Event, event_rows)
        await _bulk_insert(session, EventParticipant, participant_rows)

        stats = {
            "persons": len(person_rows),
            "names": len(name_rows),
            "families": len(family_rows),
            "family_children": len(fc_rows),
            "events": len(event_rows),
            "event_participants": len(participant_rows),
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
