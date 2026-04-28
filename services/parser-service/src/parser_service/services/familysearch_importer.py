"""FamilySearch pedigree → ORM importer (Phase 5.1).

Маппинг — см. ADR-0017. Pure-function importer:

    job = await import_fs_pedigree(
        session,
        access_token=token,
        fs_person_id="KW7S-VQJ",
        tree_id=existing_tree.id,
        owner_user_id=user.id,
        generations=4,
    )

Идемпотентность по ``provenance.fs_person_id``:

- Уже существующий FS-person с тем же id внутри ``tree_id`` → refresh
  (drop FS-provenance Names/Events этого Person'а, вставить свежие из FS).
  Manual-edited Names/Events (без FS-provenance) не трогаем.
- Не существующий — INSERT.

См. ADR-0017 §«Conflict resolution» — это **не** cross-person merge.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from familysearch_client import (
    FamilySearchClient,
    FamilySearchConfig,
    FsPedigreeNode,
    FsPerson,
)
from familysearch_client.models import FsGender
from shared_models import set_audit_skip
from shared_models.enums import (
    EntityStatus,
    EventType,
    ImportJobStatus,
    ImportSourceKind,
    NameType,
    Sex,
)
from shared_models.orm import (
    Event,
    EventParticipant,
    FsDedupAttempt,
    ImportJob,
    Name,
    Person,
    Place,
)
from shared_models.types import new_uuid
from sqlalchemy import and_, delete, insert, or_, select

from parser_service.services.fs_dedup import find_fs_dedup_candidates
from parser_service.services.metrics import import_completed_total

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_BATCH_SIZE = 5000

# FS-flagged dedup threshold (см. fs_dedup._DEFAULT_THRESHOLD). 0.6 ниже,
# чем глобальный 0.80 на ``GET /trees/{id}/duplicate-suggestions``: при
# import'е мы знаем, что новая запись — кандидат, и хотим показать
# user'у даже «возможные» (0.60–0.80) совпадения для ручного review.
_FS_DEDUP_THRESHOLD = 0.6

# Cooldown окно после reject'а пары: 90 дней. Importer не предлагает
# пару повторно, пока окно не истекло.
_FS_DEDUP_COOLDOWN_DAYS = 90

# GEDCOM-X fact short names (без http://gedcomx.org/-префикса) → наш
# EventType. Phase 5.1: только Birth/Death. Marriage — Phase 5.2 (требует
# /spouses endpoint, не входит в get_pedigree).
_FACT_TYPE_MAP: dict[str, str] = {
    "Birth": EventType.BIRTH.value,
    "Death": EventType.DEATH.value,
}


def _gedcom_xref(fs_person_id: str) -> str:
    """FamilySearch ID (``KW7S-VQJ``) → ORM gedcom_xref (``fs:KW7S-VQJ``).

    Префикс ``fs:`` отделяет FS-импорты от GEDCOM-xref'ов. См. ADR-0017
    §Person.
    """
    return f"fs:{fs_person_id}"


def _fs_url(fs_person_id: str) -> str:
    """Стабильный deeplink на FamilySearch UI."""
    return f"https://www.familysearch.org/tree/person/details/{fs_person_id}"


def _map_sex(gender: FsGender) -> str:
    # str(...) explicit — pydantic.mypy infers StrEnum.value as Any в strict.
    if gender == FsGender.MALE:
        return str(Sex.MALE.value)
    if gender == FsGender.FEMALE:
        return str(Sex.FEMALE.value)
    return str(Sex.UNKNOWN.value)


def _map_status(person: FsPerson) -> str:
    """Living person'ы из FS — HYPOTHESIS, остальные — PROBABLE.

    Mы не можем подтвердить чужие данные автоматически (CONFIRMED), но
    living-флаг повышает риск ложного матча, поэтому ниже
    (см. ADR-0017 §Person).
    """
    if person.living is True:
        return str(EntityStatus.HYPOTHESIS.value)
    return str(EntityStatus.PROBABLE.value)


def _build_provenance(
    fs_person_id: str,
    *,
    job_id: uuid.UUID,
    imported_at: dt.datetime,
) -> dict[str, Any]:
    """Provenance JSON для Person/Name/Event.

    Структура (см. ADR-0017 §«Provenance schema»):

    .. code-block:: json

        {
          "source": "familysearch",
          "fs_person_id": "KW7S-VQJ",
          "fs_url": "https://www.familysearch.org/tree/person/details/KW7S-VQJ",
          "imported_at": "2026-04-27T12:34:56+00:00",
          "import_job_id": "<UUID>"
        }
    """
    return {
        "source": "familysearch",
        "fs_person_id": fs_person_id,
        "fs_url": _fs_url(fs_person_id),
        "imported_at": imported_at.isoformat(),
        "import_job_id": str(job_id),
    }


async def _existing_fs_person_ids(
    session: AsyncSession, *, tree_id: uuid.UUID, fs_person_ids: list[str]
) -> dict[str, uuid.UUID]:
    """SELECT существующих Person'ов по provenance->>'fs_person_id'.

    Возвращает map {fs_person_id: orm_person_id}. Используется для
    идемпотентного upsert по ADR-0017 §«Conflict resolution».
    """
    if not fs_person_ids:
        return {}
    stmt = (
        select(Person.id, Person.provenance["fs_person_id"].astext.label("fs_id"))
        .where(Person.tree_id == tree_id)
        .where(Person.provenance["fs_person_id"].astext.in_(fs_person_ids))
    )
    rows = (await session.execute(stmt)).all()
    return {row.fs_id: row.id for row in rows}


async def _existing_places(
    session: AsyncSession, *, tree_id: uuid.UUID, names: list[str]
) -> dict[str, uuid.UUID]:
    """SELECT existing Place rows для tree_id по canonical_name."""
    if not names:
        return {}
    stmt = (
        select(Place.id, Place.canonical_name)
        .where(Place.tree_id == tree_id)
        .where(Place.canonical_name.in_(names))
    )
    rows = (await session.execute(stmt)).all()
    return {row.canonical_name: row.id for row in rows}


async def _drop_fs_owned_events(session: AsyncSession, *, person_ids: list[uuid.UUID]) -> int:
    """Drop existing FS-provenance Events для refresh-сценария.

    Удаляются только Event'ы с ``provenance->>'source' = 'familysearch'`` —
    user-added и GEDCOM-imported не трогаем. См. ADR-0017
    §«Сценарий 3 — events refresh».

    :class:`Name` НЕ имеет ``provenance`` колонки, поэтому имена для уже
    существующих FS-persons мы при refresh **не вставляем заново**
    (скип на caller-уровне в :func:`import_fs_pedigree`). Это сохраняет
    и уже импортированные FS-имена, и любые manually-added имена.

    Возвращает количество удалённых event-id (для статистики).
    """
    if not person_ids:
        return 0

    # Events связаны через event_participants. Сначала находим event_id
    # для FS-provenance events этих persons, удаляем participants, потом
    # сам Event.
    event_id_stmt = (
        select(Event.id)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .where(EventParticipant.person_id.in_(person_ids))
        .where(Event.provenance["source"].astext == "familysearch")
    )
    event_ids = [row.id for row in (await session.execute(event_id_stmt)).all()]
    if event_ids:
        await session.execute(
            delete(EventParticipant).where(EventParticipant.event_id.in_(event_ids))
        )
        await session.execute(delete(Event).where(Event.id.in_(event_ids)))
    return len(event_ids)


async def _bulk_insert(session: AsyncSession, model: Any, rows: list[dict[str, Any]]) -> None:
    """Чанками вставляет rows в model. No-op если rows пустой."""
    if not rows:
        return
    for start in range(0, len(rows), _BATCH_SIZE):
        chunk = rows[start : start + _BATCH_SIZE]
        await session.execute(insert(model), chunk)


def _collect_persons(tree: FsPedigreeNode) -> list[FsPerson]:
    """Pre-order collected persons (root → father subtree → mother subtree).

    Локальная реализация, чтобы избежать Any-проброса через Pydantic-метод
    при mypy strict (FsPedigreeNode.walk имеет ту же логику, но тип
    выводится через рекурсивную ``FsPedigreeNode | None``, что mypy не
    разворачивает).
    """
    result: list[FsPerson] = [tree.person]
    if tree.father is not None:
        result.extend(_collect_persons(tree.father))
    if tree.mother is not None:
        result.extend(_collect_persons(tree.mother))
    return result


def _collect_unique_places(persons: list[FsPerson]) -> list[str]:
    """Unique non-empty Place originals across Birth/Death facts."""
    seen: dict[str, None] = {}
    for p in persons:
        for fact in p.facts:
            if fact.type not in _FACT_TYPE_MAP:
                continue
            place = (fact.place_original or "").strip()
            if place:
                seen.setdefault(place, None)
    return list(seen.keys())


async def import_fs_pedigree(
    session: AsyncSession,
    *,
    access_token: str,
    fs_person_id: str,
    tree_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    generations: int = 4,
    fs_client: FamilySearchClient | None = None,
    fs_config: FamilySearchConfig | None = None,
    existing_job_id: uuid.UUID | None = None,
) -> ImportJob:
    """Импорт FS pedigree (focus + N поколений предков) в дерево.

    Args:
        session: async-сессия (commit/rollback — на caller).
        access_token: OAuth токен пользователя; сюда не сохраняется,
            используется только для одного HTTP-запроса.
        fs_person_id: FamilySearch person id (``KW7S-VQJ``).
        tree_id: существующее дерево; caller гарантирует его принадлежность
            ``owner_user_id``.
        owner_user_id: для ``ImportJob.created_by_user_id``.
        generations: число поколений предков (1..8, см. ``FamilySearchClient``).
        fs_client: optional injection — для тестов через ``pytest-httpx``;
            если ``None``, создаём собственный с ``access_token``.
        fs_config: используется только при создании собственного клиента;
            по умолчанию sandbox.
        existing_job_id: если задан, importer обновляет существующую
            ``ImportJob`` row вместо создания новой. Используется
            async-flow worker'ом (``run_fs_import_job``), который
            пред-создаёт job в HTTP-эндпоинте, чтобы вернуть user'у
            id+events_url ещё до старта worker'а.

    Returns:
        ``ImportJob`` со статусом ``succeeded`` и заполненными ``stats``.
    """
    now = dt.datetime.now(dt.UTC)

    # ---- 1. Получить или создать ImportJob, status=running ----
    if existing_job_id is not None:
        job = (
            await session.execute(select(ImportJob).where(ImportJob.id == existing_job_id))
        ).scalar_one_or_none()
        if job is None:
            msg = f"ImportJob {existing_job_id} not found (existing_job_id мode)"
            raise LookupError(msg)
        # Sanity: tree_id и source_kind должны совпадать с тем, что HTTP-уровень
        # уже зафиксировал — иначе это симптом race / неправильного вызова.
        if job.tree_id != tree_id:
            msg = (
                f"ImportJob {existing_job_id}.tree_id={job.tree_id} "
                f"does not match argument tree_id={tree_id}"
            )
            raise ValueError(msg)
        job_id = job.id
        job.status = ImportJobStatus.RUNNING.value
        if job.started_at is None:
            job.started_at = now
    else:
        job_id = new_uuid()
        job = ImportJob(
            id=job_id,
            tree_id=tree_id,
            created_by_user_id=owner_user_id,
            source_kind=ImportSourceKind.FAMILYSEARCH.value,
            source_filename=None,
            source_sha256=None,
            status=ImportJobStatus.RUNNING.value,
            started_at=now,
        )
        session.add(job)
    await session.flush()

    # ---- 2. Тянем pedigree из FS ----
    if fs_client is None:
        async with FamilySearchClient(access_token=access_token, config=fs_config) as owned_client:
            tree = await owned_client.get_pedigree(fs_person_id, generations=generations)
    else:
        tree = await fs_client.get_pedigree(fs_person_id, generations=generations)

    persons = _collect_persons(tree)
    fs_ids = [p.id for p in persons]

    # ---- 3. Lookup existing FS-persons (refresh path) ----
    existing_ids = await _existing_fs_person_ids(session, tree_id=tree_id, fs_person_ids=fs_ids)

    set_audit_skip(session.sync_session, True)
    try:
        # Drop FS-provenance Events для refresh-набора (Names не трогаем —
        # см. _drop_fs_owned_events docstring и ADR-0017 §«names/events refresh»).
        events_deleted = await _drop_fs_owned_events(
            session, person_ids=list(existing_ids.values())
        )

        # ---- 4. Resolve Place ids ----
        place_originals = _collect_unique_places(persons)
        existing_places = await _existing_places(session, tree_id=tree_id, names=place_originals)
        place_id_by_name: dict[str, uuid.UUID] = dict(existing_places)

        new_place_rows: list[dict[str, Any]] = []
        for name in place_originals:
            if name in place_id_by_name:
                continue
            new_id = new_uuid()
            place_id_by_name[name] = new_id
            new_place_rows.append(
                {
                    "id": new_id,
                    "tree_id": tree_id,
                    "canonical_name": name,
                    "status": EntityStatus.PROBABLE.value,
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": _build_provenance(fs_person_id, job_id=job_id, imported_at=now),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        await _bulk_insert(session, Place, new_place_rows)

        # ---- 5. Persons: insert new + ID-map для existing ----
        person_rows_to_insert: list[dict[str, Any]] = []
        person_id_by_fs_id: dict[str, uuid.UUID] = dict(existing_ids)
        for fs_person in persons:
            if fs_person.id in person_id_by_fs_id:
                continue
            new_id = new_uuid()
            person_id_by_fs_id[fs_person.id] = new_id
            person_rows_to_insert.append(
                {
                    "id": new_id,
                    "tree_id": tree_id,
                    "gedcom_xref": _gedcom_xref(fs_person.id),
                    "sex": _map_sex(fs_person.gender),
                    "status": _map_status(fs_person),
                    "confidence_score": 0.5,
                    "version_id": 1,
                    "provenance": _build_provenance(fs_person.id, job_id=job_id, imported_at=now),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        await _bulk_insert(session, Person, person_rows_to_insert)

        # ---- 6. Names: только для новых FS-persons ----
        # Для refreshed persons (existing_ids) — НЕ вставляем имена заново
        # (Name не имеет provenance-колонки, поэтому различить FS-добавленное
        # от manual-добавленного нельзя; сохраняем и то, и другое).
        # Trade-off: новые FS-варианты имён для существующих persons не
        # подхватываются на refresh — Phase 5.2 если потребуется.
        name_rows: list[dict[str, Any]] = []
        new_person_ids = {p["id"] for p in person_rows_to_insert}
        for fs_person in persons:
            person_pk = person_id_by_fs_id[fs_person.id]
            if person_pk not in new_person_ids:
                continue
            for sort_order, fs_name in enumerate(fs_person.names):
                # Если parts пустые, но full_text есть — кладём full_text
                # в given_name как фолбэк (Name.given_name nullable, но
                # хотим что-то отображать в UI).
                given = fs_name.given
                surname = fs_name.surname
                if given is None and surname is None and fs_name.full_text:
                    given = fs_name.full_text
                if fs_name.preferred:
                    name_type = NameType.BIRTH.value
                    sort_value = 0
                else:
                    name_type = NameType.AKA.value
                    sort_value = sort_order + 1
                name_rows.append(
                    {
                        "id": new_uuid(),
                        "person_id": person_pk,
                        "given_name": given,
                        "surname": surname,
                        "sort_order": sort_value,
                        "name_type": name_type,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
        await _bulk_insert(session, Name, name_rows)

        # ---- 7. Events + EventParticipants (Birth/Death only) ----
        event_rows: list[dict[str, Any]] = []
        participant_rows: list[dict[str, Any]] = []
        skipped_facts = 0
        for fs_person in persons:
            person_pk = person_id_by_fs_id[fs_person.id]
            for fact in fs_person.facts:
                event_type = _FACT_TYPE_MAP.get(fact.type)
                if event_type is None:
                    skipped_facts += 1
                    continue
                event_id = new_uuid()
                place_id = None
                if fact.place_original:
                    place_id = place_id_by_name.get(fact.place_original.strip())
                event_rows.append(
                    {
                        "id": event_id,
                        "tree_id": tree_id,
                        "event_type": event_type,
                        "custom_type": None,
                        "place_id": place_id,
                        "date_raw": fact.date_original,
                        "date_start": None,
                        "date_end": None,
                        "date_qualifier": None,
                        "date_calendar": None,
                        "description": None,
                        "status": EntityStatus.PROBABLE.value,
                        "confidence_score": 0.5,
                        "version_id": 1,
                        "provenance": _build_provenance(
                            fs_person.id, job_id=job_id, imported_at=now
                        ),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                participant_rows.append(
                    {
                        "id": new_uuid(),
                        "event_id": event_id,
                        "person_id": person_pk,
                        "family_id": None,
                        "role": "principal",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
        await _bulk_insert(session, Event, event_rows)
        await _bulk_insert(session, EventParticipant, participant_rows)

    finally:
        set_audit_skip(session.sync_session, False)

    # ---- 8. FS-flagged dedup attempts (Phase 5.2.1) ----
    # Только для **новых** FS-persons (refreshed уже скорились на
    # предыдущем импорте). Не блокирует success — на ошибке скорер
    # пропускаем секцию и логируем (фактически — re-raise, но importer
    # не должен зависнуть от dedup'а; сейчас оставляем raise — будет
    # видно в тестах на регрессии scorer'а).
    new_fs_person_ids = [row["id"] for row in person_rows_to_insert]
    fs_dedup_attempts_created = await _persist_fs_dedup_attempts(
        session,
        tree_id=tree_id,
        new_fs_person_ids=new_fs_person_ids,
        job_id=job_id,
        now=now,
    )

    # ---- 9. Mark job succeeded ----
    job.status = ImportJobStatus.SUCCEEDED.value
    job.finished_at = dt.datetime.now(dt.UTC)
    # ImportJobResponse.stats типизирован как dict[str, int] — поэтому
    # значения только числовые. fs_focus_person_id восстанавливается из
    # provenance любой импортированной Person.
    job.stats = {
        "persons": len(person_rows_to_insert),
        "persons_refreshed": len(existing_ids),
        "names": len(name_rows),
        "events": len(event_rows),
        "places": len(new_place_rows),
        "skipped_facts": skipped_facts,
        "events_dropped_for_refresh": events_deleted,
        "generations": generations,
        "fs_dedup_attempts_created": fs_dedup_attempts_created,
    }
    await session.flush()
    # Phase 9.0: success-инкремент; error path — в api/familysearch.py.
    import_completed_total.labels(source="fs", outcome="success").inc()
    return job


async def _persist_fs_dedup_attempts(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    new_fs_person_ids: list[uuid.UUID],
    job_id: uuid.UUID,
    now: dt.datetime,
) -> int:
    """Найти и записать ``FsDedupAttempt``-rows для свежеимпортированных FS-persons.

    Применяет три фильтра до insert'а:

    1. **fs_pid idempotency**: если для этого fs_pid уже есть row с
       ``merged_at IS NOT NULL`` — кандидат уже был ассимилирован, не
       предлагаем повторно (что бы скорер ни сказал).
    2. **Active-pair**: если есть active attempt на ту же направленную
       пару — пропускаем (партиал-уникальный индекс это enforce'ил бы
       и сам, но проверяем заранее, чтобы не ловить IntegrityError).
    3. **Cooldown**: если есть rejected attempt на ту же пару не старше
       90 дней — пропускаем (user уже отказался; не докучаем).

    Возвращает число фактически вставленных attempt-row.
    """
    if not new_fs_person_ids:
        return 0

    candidates = await find_fs_dedup_candidates(
        session,
        tree_id=tree_id,
        fs_person_ids=new_fs_person_ids,
        threshold=_FS_DEDUP_THRESHOLD,
    )
    if not candidates:
        return 0

    # 1. Idempotency: какие fs_pid уже имеют merged-attempt в этом дереве?
    fs_pids = sorted({c.fs_pid for c in candidates if c.fs_pid is not None})
    merged_fs_pids: set[str] = set()
    if fs_pids:
        merged_rows = await session.execute(
            select(FsDedupAttempt.fs_pid).where(
                FsDedupAttempt.tree_id == tree_id,
                FsDedupAttempt.fs_pid.in_(fs_pids),
                FsDedupAttempt.merged_at.isnot(None),
            )
        )
        merged_fs_pids = {row[0] for row in merged_rows.all() if row[0] is not None}

    # 2 + 3. Active-pair и cooldown: одной выборкой по всем направленным
    # парам (fs_person_id, candidate_id) этого батча.
    pair_filters = [
        and_(
            FsDedupAttempt.fs_person_id == c.fs_person_id,
            FsDedupAttempt.candidate_person_id == c.candidate_person_id,
        )
        for c in candidates
    ]
    cooldown_cutoff = now - dt.timedelta(days=_FS_DEDUP_COOLDOWN_DAYS)
    active_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    cooldown_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    if pair_filters:
        existing = await session.execute(
            select(
                FsDedupAttempt.fs_person_id,
                FsDedupAttempt.candidate_person_id,
                FsDedupAttempt.rejected_at,
                FsDedupAttempt.merged_at,
            ).where(
                FsDedupAttempt.tree_id == tree_id,
                or_(*pair_filters),
            )
        )
        for fs_pid_uuid, cand_id, rej_at, merg_at in existing.all():
            pair = (fs_pid_uuid, cand_id)
            if rej_at is None and merg_at is None:
                active_pairs.add(pair)
            elif rej_at is not None and rej_at > cooldown_cutoff:
                cooldown_pairs.add(pair)

    inserted = 0
    for cand in candidates:
        if cand.fs_pid is not None and cand.fs_pid in merged_fs_pids:
            continue
        pair = (cand.fs_person_id, cand.candidate_person_id)
        if pair in active_pairs:
            continue
        if pair in cooldown_pairs:
            continue
        session.add(
            FsDedupAttempt(
                id=new_uuid(),
                tree_id=tree_id,
                fs_person_id=cand.fs_person_id,
                candidate_person_id=cand.candidate_person_id,
                score=cand.score,
                reason="fs_import_match",
                fs_pid=cand.fs_pid,
                provenance={
                    "import_job_id": str(job_id),
                    "components": cand.components,
                },
                created_at=now,
                updated_at=now,
            )
        )
        # Track in active_pairs так чтобы дубликат-кандидат внутри одного
        # batch'а (теоретически невозможен, но defensive) не вставился
        # дважды.
        active_pairs.add(pair)
        inserted += 1
    if inserted:
        await session.flush()
    return inserted
