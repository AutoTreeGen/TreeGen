"""FS-import dedup-кандидаты (Phase 5.2.1).

После того как ``import_fs_pedigree`` вставила новые FS-persons в дерево,
этот модуль ищет среди уже существующих **не-FS** persons потенциальные
дубликаты с composite score ≥ threshold (default 0.6) и возвращает
список ``FsDedupCandidate``-tuple'ов.

Поток вызова — см. ``familysearch_importer.import_fs_pedigree``:

1. Importer передаёт сюда список ``person_ids`` тех Person'ов, которых
   он только что **создал** (refreshed FS-persons не подходят: их
   кандидаты были уже найдены при первом импорте, повторно не нужно).
2. Helper грузит ровно эти ids + всех существующих не-FS persons в
   дереве, билдит ``PersonForMatching`` DTO и считает попарный score.
3. Importer применяет idempotency / cooldown / active-pair фильтры и
   вставляет ``FsDedupAttempt`` rows.

Ничего не пишет в БД сам — это инвариант ADR-0015 (read-only scoring).

Direction matters: каждый attempt — направленная пара
``(fs_person_id, candidate_person_id)`` без lex-reorder. Об этом знает
importer, который записывает её в таком же порядке.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from entity_resolution import PersonForMatching, person_match_score
from shared_models.orm import Event, EventParticipant, Name, Person, Place
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Default threshold для FS-flagged dedup'а ниже, чем глобальный 0.80
# (ADR-0015 «likely»): FS-import — единственный момент, когда мы знаем,
# что только что положили новую запись, и более низкий порог даёт user'у
# больше кандидатов для review. Финальное решение — manual.
_DEFAULT_THRESHOLD = 0.6


@dataclass(frozen=True, slots=True)
class FsDedupCandidate:
    """Один кандидат на дедупликацию из FS-import.

    Атрибуты:
        fs_person_id: Persons.id той FS-imported персоны (новая сторона).
        fs_pid: FamilySearch external id (``Person.provenance['fs_person_id']``).
            None если provenance внезапно не содержит fs_person_id (defensive).
        candidate_person_id: Persons.id локального не-FS кандидата.
        score: Composite score в [0, 1].
        components: Покомпонентный breakdown (``person_match_score``).
    """

    fs_person_id: uuid.UUID
    fs_pid: str | None
    candidate_person_id: uuid.UUID
    score: float
    components: dict[str, float]


async def find_fs_dedup_candidates(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    fs_person_ids: list[uuid.UUID],
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[FsDedupCandidate]:
    """Найти кандидатов на дедупликацию для свежеимпортированных FS-persons.

    Args:
        session: AsyncSession. Никаких mutations.
        tree_id: дерево, в котором ищем.
        fs_person_ids: ``Persons.id`` тех записей, которые importer
            только что создал из FS pedigree. Если список пуст — возвращает
            ``[]``.
        threshold: минимальный score для попадания в результат.

    Returns:
        Отсортированный по убыванию ``score`` список
        ``FsDedupCandidate``. Может быть пустым.
    """
    if not fs_person_ids:
        return []

    fs_set = set(fs_person_ids)

    persons_rows = (
        (
            await session.execute(
                select(Person).where(
                    Person.tree_id == tree_id,
                    Person.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not persons_rows:
        return []

    # FS-сторона: только что импортированные persons из fs_set.
    fs_persons = [p for p in persons_rows if p.id in fs_set]
    if not fs_persons:
        return []

    # Кандидаты: не-FS persons (по provenance.source). Persons из fs_set
    # сюда не попадают — direction matters: матчим FS → local, не наоборот.
    candidate_persons = [
        p
        for p in persons_rows
        if p.id not in fs_set and (p.provenance or {}).get("source") != "familysearch"
    ]
    if not candidate_persons:
        return []

    # Bulk-load имена и BIRT/DEAT events для всех релевантных person_ids
    # (FS + кандидаты), чтобы заполнить PersonForMatching без N+1.
    relevant_ids = [p.id for p in fs_persons] + [p.id for p in candidate_persons]

    name_by_person = await _load_primary_names(session, relevant_ids)
    birt_year, deat_year, birt_place = await _load_birt_deat(session, relevant_ids)

    def _build_pfm(p: Person) -> PersonForMatching:
        n = name_by_person.get(p.id)
        return PersonForMatching(
            given=n.given_name if n else None,
            surname=n.surname if n else None,
            birth_year=birt_year.get(p.id),
            death_year=deat_year.get(p.id),
            birth_place=birt_place.get(p.id),
            sex=p.sex,
        )

    fs_dtos: list[tuple[Person, PersonForMatching]] = [(p, _build_pfm(p)) for p in fs_persons]
    cand_dtos: list[tuple[Person, PersonForMatching]] = [
        (p, _build_pfm(p)) for p in candidate_persons
    ]

    out: list[FsDedupCandidate] = []
    for fs_p, fs_pfm in fs_dtos:
        fs_pid = (fs_p.provenance or {}).get("fs_person_id")
        for cand_p, cand_pfm in cand_dtos:
            score, components = person_match_score(fs_pfm, cand_pfm)
            if score < threshold:
                continue
            out.append(
                FsDedupCandidate(
                    fs_person_id=fs_p.id,
                    fs_pid=fs_pid if isinstance(fs_pid, str) else None,
                    candidate_person_id=cand_p.id,
                    score=score,
                    components=components,
                )
            )
    out.sort(key=lambda c: c.score, reverse=True)
    return out


async def _load_primary_names(
    session: AsyncSession, person_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Name]:
    """Имена с ``sort_order=0`` для каждого person_id (одно на персону)."""
    if not person_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Name)
                .where(Name.person_id.in_(person_ids), Name.sort_order == 0)
                .order_by(Name.person_id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, Name] = {}
    for n in rows:
        out.setdefault(n.person_id, n)
    return out


async def _load_birt_deat(
    session: AsyncSession, person_ids: list[uuid.UUID]
) -> tuple[
    dict[uuid.UUID, int | None],
    dict[uuid.UUID, int | None],
    dict[uuid.UUID, str | None],
]:
    """BIRT/DEAT year + birth_place per person, через event_participants."""
    if not person_ids:
        return {}, {}, {}
    rows = (
        await session.execute(
            select(Event, EventParticipant.person_id, Place.canonical_name)
            .join(EventParticipant, EventParticipant.event_id == Event.id)
            .join(Place, Place.id == Event.place_id, isouter=True)
            .where(
                EventParticipant.person_id.in_(person_ids),
                Event.deleted_at.is_(None),
                Event.event_type.in_(("BIRT", "DEAT")),
            )
        )
    ).all()
    birt_year: dict[uuid.UUID, int | None] = {}
    deat_year: dict[uuid.UUID, int | None] = {}
    birt_place: dict[uuid.UUID, str | None] = {}
    for event, person_id, place_name in rows:
        year = event.date_start.year if event.date_start else None
        if event.event_type == "BIRT":
            birt_year.setdefault(person_id, year)
            if place_name:
                birt_place.setdefault(person_id, place_name)
        elif event.event_type == "DEAT":
            deat_year.setdefault(person_id, year)
    return birt_year, deat_year, birt_place
