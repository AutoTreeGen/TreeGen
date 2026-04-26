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

from shared_models import (
    register_audit_listeners,
    set_audit_skip,
)
from shared_models.enums import (
    ActorKind,
    AuditAction,
    EntityStatus,
    ImportJobStatus,
    ImportSourceKind,
    NameType,
    Sex,
)
from shared_models.orm import (
    AuditLog,
    Family,
    FamilyChild,
    ImportJob,
    Name,
    Person,
    Tree,
    User,
)
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
        return Sex.MALE.value
    if value == "F":
        return Sex.FEMALE.value
    if value == "X":
        return Sex.OTHER.value
    return Sex.UNKNOWN.value


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
        display_name=email.split("@")[0],
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
) -> ImportJob:
    """Распарсить GEDCOM-файл и записать в БД.

    Args:
        session: Активная async-сессия (commit/rollback — на caller).
        ged_path: Локальный путь к .ged файлу.
        owner_email: Email user'а-владельца дерева. Создаётся, если нет.
        tree_name: Имя нового дерева. По умолчанию — basename файла.

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

    register_audit_listeners(session.sync_session.bind.sync_engine)

    sha = _sha256(ged_path)
    owner = await _ensure_owner(session, owner_email)

    tree = Tree(
        owner_user_id=owner.id,
        name=tree_name or ged_path.stem,
        visibility="private",
        default_locale="en",
        settings={},
        provenance={"source_filename": ged_path.name, "source_sha256": sha},
        version_id=1,
        status=EntityStatus.CONFIRMED.value,
        confidence_score=1.0,
    )
    session.add(tree)
    await session.flush()

    job = ImportJob(
        tree_id=tree.id,
        owner_user_id=owner.id,
        source_kind=ImportSourceKind.GEDCOM.value,
        source_filename=ged_path.name,
        source_sha256=sha,
        status=ImportJobStatus.PROCESSING.value,
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
            from shared_models.types import new_uuid

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
                from shared_models.types import new_uuid

                name_rows.append(
                    {
                        "id": new_uuid(),
                        "person_id": person_id,
                        "given_name": name.given,
                        "surname": name.surname,
                        "sort_order": sort_order,
                        "name_type": NameType.BIRTH.value if sort_order == 0 else NameType.AKA.value,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
        await _bulk_insert(session, Name, name_rows)

        # ---- Families + family_children ----
        family_rows: list[dict[str, Any]] = []
        family_id_by_xref: dict[str, Any] = {}
        for xref, family in document.families.items():
            from shared_models.types import new_uuid

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
                from shared_models.types import new_uuid

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

        stats = {
            "persons": len(person_rows),
            "names": len(name_rows),
            "families": len(family_rows),
            "family_children": len(fc_rows),
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
            reason=f"API import of {ged_path.name}",
            diff={"summary": stats, "source_sha256": sha, "fields": list(stats.keys())},
        )
    )
    return job
