"""Сервис: применение dedup-алгоритмов к содержимому БД одного дерева.

Использует pure-function скоринг из ``packages/entity-resolution/``.
Сам сервис **READ-ONLY** — только SELECT-запросы, никаких UPDATE /
DELETE. Это инвариант ADR-0015 + CLAUDE.md §5: финальный merge — через
UI Phase 4.5 с manual approval. Тест ``test_no_database_mutations``
явно это проверяет.

Сигнатура каждой функции одинакова: ``(session, tree_id, threshold,
**опции) -> list[DuplicateSuggestion]``. Threshold по умолчанию 0.80
(см. ADR-0015 confidence levels).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from entity_resolution import (
    PersonForMatching,
    block_by_dm,
    person_match_score,
    place_match_score,
    source_match_score,
)
from shared_models.orm import Event, Name, Person, Place, Source
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.schemas import DuplicateSuggestion

_DEFAULT_THRESHOLD = 0.80
# Sentinel: список tree_id-фильтра одного дерева (cross-tree dedup —
# отдельный ADR, см. ADR-0015 §«Когда пересмотреть»).


# -----------------------------------------------------------------------------
# Sources
# -----------------------------------------------------------------------------


async def find_source_duplicates(
    session: AsyncSession,
    tree_id: uuid.UUID,
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[DuplicateSuggestion]:
    """Найти пары sources с composite score ≥ threshold.

    O(N²) — sources обычно их меньше 1000, blocking не нужен. Для
    каждой пары вызываем ``source_match_score`` (token_set title +
    Jaccard authors + abbrev exact match). Source ORM не содержит
    abbreviation column, поэтому передаём ``None`` — ADR-0015 это
    допускает (вес перераспределяется).
    """
    result = await session.execute(
        select(Source).where(
            Source.tree_id == tree_id,
            Source.deleted_at.is_(None),
        )
    )
    sources = list(result.scalars().all())

    suggestions: list[DuplicateSuggestion] = []
    for i, a in enumerate(sources):
        for b in sources[i + 1 :]:
            score = source_match_score(
                a.title,
                a.author,
                None,  # abbreviation: пока не храним, см. ADR-0015
                b.title,
                b.author,
                None,
            )
            if score < threshold:
                continue
            suggestions.append(
                DuplicateSuggestion(
                    entity_type="source",
                    entity_a_id=a.id,
                    entity_b_id=b.id,
                    confidence=score,
                    components={"composite": score},
                    evidence={
                        "a_title": a.title,
                        "b_title": b.title,
                        "a_author": a.author,
                        "b_author": b.author,
                    },
                )
            )
    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return suggestions


# -----------------------------------------------------------------------------
# Places
# -----------------------------------------------------------------------------


async def find_place_duplicates(
    session: AsyncSession,
    tree_id: uuid.UUID,
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[DuplicateSuggestion]:
    """Найти пары places с place_match_score ≥ threshold.

    O(N²) на canonical_name. Иерархический prefix-subset boost
    встроен в ``place_match_score`` (Slonim ⊂ Slonim, Grodno → ≥0.85).
    """
    result = await session.execute(
        select(Place).where(
            Place.tree_id == tree_id,
            Place.deleted_at.is_(None),
        )
    )
    places = list(result.scalars().all())

    suggestions: list[DuplicateSuggestion] = []
    for i, a in enumerate(places):
        for b in places[i + 1 :]:
            score = place_match_score(a.canonical_name, b.canonical_name)
            if score < threshold:
                continue
            suggestions.append(
                DuplicateSuggestion(
                    entity_type="place",
                    entity_a_id=a.id,
                    entity_b_id=b.id,
                    confidence=score,
                    components={"composite": score},
                    evidence={
                        "a_name": a.canonical_name,
                        "b_name": b.canonical_name,
                    },
                )
            )
    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return suggestions


# -----------------------------------------------------------------------------
# Persons
# -----------------------------------------------------------------------------


async def find_person_duplicates(
    session: AsyncSession,
    tree_id: uuid.UUID,
    threshold: float = _DEFAULT_THRESHOLD,
    *,
    use_blocking: bool = True,
) -> list[DuplicateSuggestion]:
    """Найти пары persons с composite score ≥ threshold.

    Args:
        session: AsyncSession.
        tree_id: фильтр по дереву.
        threshold: минимальный confidence (0..1).
        use_blocking: если True (default) — DM-блокирование по surname,
            O(N × bucket_size). Для small trees (<500 persons) разница
            незаметна; для крупных деревьев blocking снижает время на
            порядки.
    """
    persons_data = await _load_persons_for_matching(session, tree_id)
    if not persons_data:
        return []

    if use_blocking:
        candidate_pairs = _candidate_pairs_via_blocking(persons_data)
    else:
        candidate_pairs = _candidate_pairs_naive(persons_data)

    suggestions: list[DuplicateSuggestion] = []
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for a_id, a, b_id, b in candidate_pairs:
        # Один и тот же кандидат может попасть в несколько DM-bucket'ов
        # → дедупим явно по упорядоченной паре id.
        pair_key = (a_id, b_id) if str(a_id) < str(b_id) else (b_id, a_id)
        if pair_key in seen:
            continue
        seen.add(pair_key)

        score, components = person_match_score(a, b)
        if score < threshold:
            continue
        suggestions.append(
            DuplicateSuggestion(
                entity_type="person",
                entity_a_id=a_id,
                entity_b_id=b_id,
                confidence=score,
                components=components,
                evidence={
                    "a_name": _format_name(a),
                    "b_name": _format_name(b),
                    "a_birth_year": a.birth_year,
                    "b_birth_year": b.birth_year,
                    "a_birth_place": a.birth_place,
                    "b_birth_place": b.birth_place,
                },
            )
        )
    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return suggestions


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _format_name(p: PersonForMatching) -> str:
    parts = [p.given or "?", p.surname or "?"]
    return " ".join(parts)


async def _load_persons_for_matching(
    session: AsyncSession,
    tree_id: uuid.UUID,
) -> list[tuple[uuid.UUID, PersonForMatching]]:
    """Подтянуть persons + их sort_order=0 имя + BIRT-year+место + DEAT-year.

    Делает 3 SELECT'а (persons, names, BIRT/DEAT events) и собирает в
    памяти, чтобы избежать N+1. Tradeoff: для очень больших деревьев
    (100k+) лучше будет потоковый подход с блокированием на уровне БД,
    но для MVP это норма.
    """
    persons_res = await session.execute(
        select(Person).where(
            Person.tree_id == tree_id,
            Person.deleted_at.is_(None),
        )
    )
    persons = list(persons_res.scalars().all())
    if not persons:
        return []
    person_ids = [p.id for p in persons]

    # sort_order=0 имя на person.
    names_res = await session.execute(
        select(Name)
        .where(Name.person_id.in_(person_ids), Name.sort_order == 0)
        .order_by(Name.person_id)
    )
    name_by_person: dict[uuid.UUID, Name] = {}
    for name in names_res.scalars().all():
        name_by_person.setdefault(name.person_id, name)

    # BIRT/DEAT события + place. Используем event_participants чтобы найти
    # «персональные» события (т.к. для FAM-events участники — husband/wife).
    from shared_models.orm import EventParticipant  # noqa: PLC0415 — локальный импорт

    events_res = await session.execute(
        select(Event, EventParticipant.person_id, Place.canonical_name)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .join(Place, Place.id == Event.place_id, isouter=True)
        .where(
            EventParticipant.person_id.in_(person_ids),
            Event.deleted_at.is_(None),
            Event.event_type.in_(("BIRT", "DEAT")),
        )
    )
    birt_year: dict[uuid.UUID, int | None] = {}
    deat_year: dict[uuid.UUID, int | None] = {}
    birt_place: dict[uuid.UUID, str | None] = {}
    for event, person_id, place_name in events_res.all():
        year = event.date_start.year if event.date_start else None
        if event.event_type == "BIRT":
            birt_year.setdefault(person_id, year)
            if place_name:
                birt_place.setdefault(person_id, place_name)
        elif event.event_type == "DEAT":
            deat_year.setdefault(person_id, year)

    # Сборка PersonForMatching.
    out: list[tuple[uuid.UUID, PersonForMatching]] = []
    for p in persons:
        n = name_by_person.get(p.id)
        out.append(
            (
                p.id,
                PersonForMatching(
                    given=n.given_name if n else None,
                    surname=n.surname if n else None,
                    birth_year=birt_year.get(p.id),
                    death_year=deat_year.get(p.id),
                    birth_place=birt_place.get(p.id),
                    sex=p.sex,
                ),
            )
        )
    return out


def _candidate_pairs_naive(
    persons_data: list[tuple[uuid.UUID, PersonForMatching]],
) -> list[tuple[uuid.UUID, PersonForMatching, uuid.UUID, PersonForMatching]]:
    out: list[tuple[uuid.UUID, PersonForMatching, uuid.UUID, PersonForMatching]] = []
    for i, (a_id, a) in enumerate(persons_data):
        for b_id, b in persons_data[i + 1 :]:
            out.append((a_id, a, b_id, b))
    return out


def _candidate_pairs_via_blocking(
    persons_data: list[tuple[uuid.UUID, PersonForMatching]],
) -> list[tuple[uuid.UUID, PersonForMatching, uuid.UUID, PersonForMatching]]:
    """Сгенерировать candidate-пары через DM-bucket блокирование."""
    # Map из PersonForMatching id() обратно к (uuid, person) — block_by_dm
    # принимает Iterable[PersonForMatching] без id'шек.
    by_pfm_id: dict[int, tuple[uuid.UUID, PersonForMatching]] = {
        id(pfm): (pid, pfm) for pid, pfm in persons_data
    }
    buckets = block_by_dm(pfm for _, pfm in persons_data)

    out: list[tuple[uuid.UUID, PersonForMatching, uuid.UUID, PersonForMatching]] = []
    for bucket in buckets.values():
        # Naive O(k²) внутри bucket'а, но bucket << total.
        for i, pfm_a in enumerate(bucket):
            a_id, a = by_pfm_id[id(pfm_a)]
            for pfm_b in bucket[i + 1 :]:
                b_id, b = by_pfm_id[id(pfm_b)]
                out.append((a_id, a, b_id, b))
    return out


# -----------------------------------------------------------------------------
# Public re-exports / counters (полезно для performance тестов).
# -----------------------------------------------------------------------------


def _bucket_size_histogram(
    persons_data: list[tuple[uuid.UUID, PersonForMatching]],
) -> dict[str, int]:
    """Diagnostic: сколько persons в каждом DM-bucket'е."""
    buckets = block_by_dm(pfm for _, pfm in persons_data)
    sizes: dict[str, int] = defaultdict(int)
    for code, persons in buckets.items():
        sizes[code] = len(persons)
    return dict(sizes)


__all__: list[Any] = [
    "find_person_duplicates",
    "find_place_duplicates",
    "find_source_duplicates",
]
