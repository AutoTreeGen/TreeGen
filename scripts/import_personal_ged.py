"""Bulk-импорт личного GED-файла (Ztree.ged) в локальную БД.

Использует ``gedcom_parser`` для парсинга и SQLAlchemy bulk INSERT для записи.

Производительность:

- Старая версия (per-row session.add + flush после каждой семьи + полный audit):
  ~30-40 минут на 61k персон.
- Эта версия (bulk INSERT по 5000 + audit отключён): ~30 секунд.

Provenance не теряется: каждая сущность получает ``provenance.import_job_id``
(jsonb), а ``import_jobs`` хранит SHA-256 файла для идемпотентности (Phase 7).
Один агрегированный audit-entry уровня ``import_job`` пишется в конце.

Запуск:
    uv run python scripts/import_personal_ged.py [path]

ENV:
    DATABASE_URL — postgresql+asyncpg://...
    AUTOTREEGEN_OWNER_EMAIL — email владельца дерева.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import os
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Windows + Python 3.13: ProactorEventLoop ломает asyncpg/psycopg async (SCRAM).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Автозагрузка .env: иначе DATABASE_URL/POSTGRES_PASSWORD не подхватятся.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

from shared_models import (
    orm,  # noqa: F401  — регистрируем модели
    register_audit_listeners,
    set_audit_skip,
)
from shared_models.audit import AuditContext, set_audit_context
from shared_models.enums import (
    ActorKind,
    AuditAction,
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Маппинг GEDCOM-тегов в наш EventType.
_EVENT_TAG_TO_TYPE: dict[str, EventType] = {
    "BIRT": EventType.BIRTH,
    "DEAT": EventType.DEATH,
    "MARR": EventType.MARRIAGE,
    "DIV": EventType.DIVORCE,
    "BAPM": EventType.BAPTISM,
    "CHR": EventType.CHRISTENING,
    "BURI": EventType.BURIAL,
    "CREM": EventType.CREMATION,
    "RESI": EventType.RESIDENCE,
    "EMIG": EventType.EMIGRATION,
    "IMMI": EventType.IMMIGRATION,
    "NATU": EventType.NATURALIZATION,
    "CENS": EventType.CENSUS,
    "OCCU": EventType.OCCUPATION,
}

_BATCH_SIZE = 5000


def _map_sex(value: str | None) -> str:
    """GEDCOM SEX → enum Sex."""
    if value in {"M", "F", "U", "X"}:
        return value
    return Sex.UNKNOWN.value


def _map_event_type(tag: str) -> tuple[str, str | None]:
    """GEDCOM event-tag → (EventType, custom_type)."""
    mapped = _EVENT_TAG_TO_TYPE.get(tag.upper())
    if mapped is not None:
        return mapped.value, None
    return EventType.CUSTOM.value, tag


async def _ensure_owner(session: Any, email: str) -> User:
    """Найти или создать пользователя-владельца дерева."""
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        return existing  # type: ignore[no-any-return]
    user = User(email=email, external_auth_id=f"import-script|{email}", display_name="Owner")
    session.add(user)
    await session.flush()
    return user


def _chunk(seq: list[dict[str, Any]], size: int) -> Sequence[list[dict[str, Any]]]:
    """Разбить список dict'ов на чанки фиксированного размера."""
    return [seq[i : i + size] for i in range(0, len(seq), size)]


async def _bulk_insert(session: Any, model: Any, rows: list[dict[str, Any]]) -> None:
    """Bulk INSERT с разбивкой на батчи."""
    if not rows:
        return
    for chunk in _chunk(rows, _BATCH_SIZE):
        await session.execute(insert(model), chunk)


async def _import(database_url: str, ged_path: Path, owner_email: str) -> None:
    """Прогнать парсер по файлу и записать в БД через bulk INSERT."""
    from gedcom_parser import parse_document_file  # type: ignore[import-not-found]

    if not ged_path.exists():
        sys.exit(f"ERROR: file not found: {ged_path}")

    raw_bytes = ged_path.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    print(f"[import] file={ged_path} size={len(raw_bytes)} sha256={sha[:12]}")

    print("[import] parsing GEDCOM...")
    # Глушим warnings парсера (грязные даты в реальных GED) — они не блокеры,
    # будут обработаны в Phase 1 backlog (расширение date parser).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        doc = parse_document_file(ged_path, lenient=True)
    print(f"[import] parsed: persons={len(doc.persons)} families={len(doc.families)}")

    engine = create_async_engine(database_url, echo=False, future=True)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
    register_audit_listeners(SessionMaker)

    async with SessionMaker() as session, session.begin():
        # ---- Setup: owner, tree, import_job (с audit'ом) -----------------
        owner = await _ensure_owner(session, owner_email)
        tree = Tree(owner_user_id=owner.id, name=ged_path.stem, default_locale="ru")
        session.add(tree)
        await session.flush()

        job = ImportJob(
            tree_id=tree.id,
            created_by_user_id=owner.id,
            source_kind=ImportSourceKind.GEDCOM.value,
            source_filename=ged_path.name,
            source_size_bytes=len(raw_bytes),
            source_sha256=sha,
            status=ImportJobStatus.RUNNING.value,
            started_at=dt.datetime.now(dt.UTC),
        )
        session.add(job)
        await session.flush()

        set_audit_context(
            session.sync_session,
            AuditContext(
                actor_user_id=owner.id,
                actor_kind=ActorKind.IMPORT_JOB,
                import_job_id=job.id,
                reason=f"bulk import {ged_path.name}",
            ),
        )

        # ---- Bulk-фаза: audit отключён, пишем построчным provenance.import_job_id
        set_audit_skip(session.sync_session, True)

        provenance = {"source_files": [ged_path.name], "import_job_id": str(job.id)}
        now = dt.datetime.now(dt.UTC)

        # Persons
        person_rows: list[dict[str, Any]] = []
        person_id_by_xref: dict[str, Any] = {}
        for xref, parsed in doc.persons.items():
            pid = new_uuid()
            person_id_by_xref[xref] = pid
            person_rows.append(
                {
                    "id": pid,
                    "tree_id": tree.id,
                    "gedcom_xref": xref,
                    "sex": _map_sex(parsed.sex),
                    "merged_into_person_id": None,
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "provenance": provenance,
                    "version_id": 1,
                    "created_at": now,
                    "updated_at": now,
                    "deleted_at": None,
                }
            )
        await _bulk_insert(session, Person, person_rows)
        print(f"[import] inserted {len(person_rows)} persons")

        # Names
        name_rows: list[dict[str, Any]] = []
        for xref, parsed in doc.persons.items():
            pid = person_id_by_xref[xref]
            for i, n in enumerate(parsed.names):
                name_rows.append(
                    {
                        "id": new_uuid(),
                        "person_id": pid,
                        "given_name": getattr(n, "given", None),
                        "surname": getattr(n, "surname", None),
                        "prefix": None,
                        "suffix": getattr(n, "suffix", None),
                        "nickname": None,
                        "patronymic": None,
                        "maiden_surname": None,
                        "name_type": NameType.BIRTH.value,
                        "script": None,
                        "romanized": None,
                        "sort_order": i,
                        "created_at": now,
                        "updated_at": now,
                        "deleted_at": None,
                    }
                )
        await _bulk_insert(session, Name, name_rows)
        print(f"[import] inserted {len(name_rows)} names")

        # Families
        family_rows: list[dict[str, Any]] = []
        family_id_by_xref: dict[str, Any] = {}
        for xref, parsed_fam in doc.families.items():
            fid = new_uuid()
            family_id_by_xref[xref] = fid
            family_rows.append(
                {
                    "id": fid,
                    "tree_id": tree.id,
                    "gedcom_xref": xref,
                    "husband_id": (
                        person_id_by_xref.get(parsed_fam.husband_xref)
                        if parsed_fam.husband_xref
                        else None
                    ),
                    "wife_id": (
                        person_id_by_xref.get(parsed_fam.wife_xref)
                        if parsed_fam.wife_xref
                        else None
                    ),
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "provenance": provenance,
                    "version_id": 1,
                    "created_at": now,
                    "updated_at": now,
                    "deleted_at": None,
                }
            )
        await _bulk_insert(session, Family, family_rows)
        print(f"[import] inserted {len(family_rows)} families")

        # FamilyChildren
        fc_rows: list[dict[str, Any]] = []
        for xref, parsed_fam in doc.families.items():
            fid = family_id_by_xref[xref]
            for order, child_xref in enumerate(parsed_fam.children_xrefs):
                cid = person_id_by_xref.get(child_xref)
                if cid is None:
                    continue
                fc_rows.append(
                    {
                        "id": new_uuid(),
                        "family_id": fid,
                        "child_person_id": cid,
                        "relation_type": "biological",
                        "birth_order": order,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
        await _bulk_insert(session, FamilyChild, fc_rows)
        print(f"[import] inserted {len(fc_rows)} family_children links")

        # Events + EventParticipants
        event_rows: list[dict[str, Any]] = []
        ep_rows: list[dict[str, Any]] = []

        for xref, parsed_fam in doc.families.items():
            fid = family_id_by_xref[xref]
            for ev in parsed_fam.events:
                ev_type, custom = _map_event_type(ev.tag)
                eid = new_uuid()
                event_rows.append(
                    {
                        "id": eid,
                        "tree_id": tree.id,
                        "event_type": ev_type,
                        "custom_type": custom,
                        "place_id": None,
                        "date_raw": getattr(ev, "date_raw", None),
                        "date_start": None,
                        "date_end": None,
                        "date_qualifier": None,
                        "date_calendar": None,
                        "description": getattr(ev, "description", None),
                        "status": EntityStatus.PROBABLE.value,
                        "confidence_score": 0.5,
                        "provenance": provenance,
                        "version_id": 1,
                        "created_at": now,
                        "updated_at": now,
                        "deleted_at": None,
                    }
                )
                ep_rows.append(
                    {
                        "id": new_uuid(),
                        "event_id": eid,
                        "person_id": None,
                        "family_id": fid,
                        "role": "principal",
                        "created_at": now,
                        "updated_at": now,
                    }
                )

        for xref, parsed in doc.persons.items():
            pid = person_id_by_xref[xref]
            for ev in parsed.events:
                ev_type, custom = _map_event_type(ev.tag)
                eid = new_uuid()
                event_rows.append(
                    {
                        "id": eid,
                        "tree_id": tree.id,
                        "event_type": ev_type,
                        "custom_type": custom,
                        "place_id": None,
                        "date_raw": getattr(ev, "date_raw", None),
                        "date_start": None,
                        "date_end": None,
                        "date_qualifier": None,
                        "date_calendar": None,
                        "description": getattr(ev, "description", None),
                        "status": EntityStatus.PROBABLE.value,
                        "confidence_score": 0.5,
                        "provenance": provenance,
                        "version_id": 1,
                        "created_at": now,
                        "updated_at": now,
                        "deleted_at": None,
                    }
                )
                ep_rows.append(
                    {
                        "id": new_uuid(),
                        "event_id": eid,
                        "person_id": pid,
                        "family_id": None,
                        "role": "principal",
                        "created_at": now,
                        "updated_at": now,
                    }
                )

        await _bulk_insert(session, Event, event_rows)
        await _bulk_insert(session, EventParticipant, ep_rows)
        print(f"[import] inserted {len(event_rows)} events + {len(ep_rows)} participants")

        # ---- Финал: один агрегированный audit-entry уровня import_job ----
        set_audit_skip(session.sync_session, False)

        stats = {
            "persons": len(person_rows),
            "names": len(name_rows),
            "families": len(family_rows),
            "family_children": len(fc_rows),
            "events": len(event_rows),
            "event_participants": len(ep_rows),
        }
        job.status = ImportJobStatus.SUCCEEDED.value
        job.stats = stats
        job.finished_at = dt.datetime.now(dt.UTC)

        # Один summary-entry в audit_log вместо тысяч построчных.
        session.add(
            AuditLog(
                tree_id=tree.id,
                entity_type="import_jobs",
                entity_id=job.id,
                action=AuditAction.INSERT.value,
                actor_user_id=owner.id,
                actor_kind=ActorKind.IMPORT_JOB.value,
                import_job_id=job.id,
                reason=f"bulk import of {ged_path.name}",
                diff={
                    "summary": stats,
                    "source_sha256": sha,
                    "fields": list(stats.keys()),
                },
            )
        )

        await session.commit()
        print(f"[import] OK: tree={tree.id}  {stats}")

    await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Bulk-импорт личного GEDCOM в локальную БД")
    parser.add_argument(
        "path", nargs="?", default="Ztree.ged", help="Путь к GED (default: ./Ztree.ged)"
    )
    args = parser.parse_args(argv)

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://autotreegen:autotreegen@localhost:5432/autotreegen",
    )
    if not db_url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    owner_email = os.getenv("AUTOTREEGEN_OWNER_EMAIL", "owner@autotreegen.local")

    asyncio.run(_import(db_url, Path(args.path).resolve(), owner_email))


if __name__ == "__main__":
    main()
