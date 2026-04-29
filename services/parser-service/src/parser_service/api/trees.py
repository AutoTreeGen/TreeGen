"""Trees API: list persons in a tree + person detail + ancestors pedigree."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models import TreeRole
from shared_models.orm import (
    Citation,
    DnaMatch,
    EntityMultimedia,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Hypothesis,
    MultimediaObject,
    Name,
    Person,
    Place,
    Source,
    Tree,
)
from sqlalchemy import ColumnElement, and_, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from parser_service.database import get_session
from parser_service.schemas import (
    AncestorsResponse,
    AncestorTreeNode,
    CitationSummary,
    EventSummary,
    MultimediaSummary,
    NameSummary,
    PersonDetail,
    PersonListResponse,
    PersonSummary,
    TopSurname,
    TreeStatisticsResponse,
)
from parser_service.services.dm_buckets import compute_dm_buckets
from parser_service.services.permissions import require_tree_role

router = APIRouter()


@router.get(
    "/trees/{tree_id}/persons",
    response_model=PersonListResponse,
    summary="Paginated list of persons in a tree",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def list_persons(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> PersonListResponse:
    """List of persons with primary_name."""
    total = await session.scalar(
        select(func.count(Person.id)).where(
            Person.tree_id == tree_id,
            Person.deleted_at.is_(None),
        )
    )
    res = await session.execute(
        select(Person)
        .where(Person.tree_id == tree_id, Person.deleted_at.is_(None))
        .order_by(Person.created_at)
        .limit(limit)
        .offset(offset)
    )
    persons = res.scalars().all()

    items: list[PersonSummary] = []
    for p in persons:
        primary = next(
            (
                f"{n.given_name or ''} {n.surname or ''}".strip()
                for n in sorted(p.names, key=lambda n: n.sort_order)
                if n.given_name or n.surname
            ),
            None,
        )
        items.append(
            PersonSummary(
                id=p.id,
                gedcom_xref=p.gedcom_xref,
                sex=p.sex,
                confidence_score=p.confidence_score,
                primary_name=primary,
            )
        )
    return PersonListResponse(
        tree_id=tree_id,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/trees/{tree_id}/persons/search",
    response_model=PersonListResponse,
    summary="Search persons in a tree by name (substring or phonetic) and birth-year range",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def search_persons(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[
        str | None,
        Query(
            description=(
                "По умолчанию — case-insensitive substring search (ILIKE) "
                "по given_name / surname / их конкатенации. С ``phonetic=true`` "
                "переключается на Daitch-Mokotoff bucket-overlap по "
                "``persons.surname_dm`` / ``persons.given_name_dm``. "
                "Пусто/None — не фильтрует. SQL-injection-safe."
            ),
            max_length=200,
        ),
    ] = None,
    phonetic: Annotated[
        bool,
        Query(
            description=(
                "Phonetic-режим (Daitch-Mokotoff). Когда ``true``, ``q`` "
                "транслитерируется (cyrillic→latin), считаются DM-buckets, "
                "и Postgres ARRAY overlap (``&&``) находит персон, чьи "
                "``surname_dm`` или ``given_name_dm`` пересекаются. Phase 4.4.1."
            ),
        ),
    ] = False,
    birth_year_min: Annotated[
        int | None,
        Query(
            ge=1,
            le=9999,
            description="Минимум года рождения из BIRT-события (date_start.year ≥ X).",
        ),
    ] = None,
    birth_year_max: Annotated[
        int | None,
        Query(
            ge=1,
            le=9999,
            description="Максимум года рождения из BIRT-события (date_start.year ≤ X).",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PersonListResponse:
    """Search persons by name + birth-year range, with optional phonetic mode.

    Возвращает тот же ``PersonListResponse`` что и list-эндпоинт. ``items[].match_type``
    подсказывает фронту, через какой механизм найден ряд: ``substring`` для
    дефолтного ILIKE, ``phonetic`` когда сработал DM bucket-overlap, ``None``
    если ``q`` не передан (просто list).

    Tree existence: 404 если ``tree_id`` не существует.

    Phonetic-режим (Phase 4.4.1):
    - ``q`` транслитерируется через ``transliterate_cyrillic`` (Ж→ZH, …),
      потому что DM работает только на A-Z. Так фамилия ``Жытницкий``
      даёт те же DM-bucket'ы что и ``Zhitnitzky``.
    - Поиск идёт по предвычисленным колонкам ``persons.surname_dm`` /
      ``persons.given_name_dm`` через operator ``&&`` (arrays overlap),
      покрытым GIN-индексами. Без JOIN'а на ``names`` — sub-50ms на 12k
      персонах.
    - Если по ``q`` не получилось DM-кодов (например, ``q="123"`` или ``q="-"``),
      ничего не возвращаем (а не fall back в substring) — иначе UI
      отдаёт ``substring`` matches при включённой Phonetic-галочке, что путает.

    Birth year фильтр через EXISTS-подзапрос на BIRT-событие с
    ``date_start.year`` в диапазоне. Можно комбинировать с любым из q-режимов.
    """
    tree_exists = await session.scalar(
        select(func.count()).select_from(Tree).where(Tree.id == tree_id)
    )
    if not tree_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {tree_id} not found",
        )

    base_filters = [Person.tree_id == tree_id, Person.deleted_at.is_(None)]
    # match_type вычисляется отдельно для items; один и тот же endpoint
    # отдаёт substring или phonetic в зависимости от ?phonetic=true.
    item_match_type: str | None = None

    if q and phonetic:
        # Phonetic путь: DM-bucket overlap по surname_dm / given_name_dm.
        dm_codes = compute_dm_buckets(q)
        if not dm_codes:
            # Запрос не даёт ни одного DM-кода (только цифры / пунктуация).
            # Эквивалентно «ничего не нашли», без fallback в substring.
            return PersonListResponse(
                tree_id=tree_id,
                total=0,
                limit=limit,
                offset=offset,
                items=[],
            )
        item_match_type = "phonetic"
        # SQLAlchemy ARRAY.overlap — Postgres operator `&&`.
        base_filters.append(
            or_(
                Person.surname_dm.overlap(dm_codes),
                Person.given_name_dm.overlap(dm_codes),
            )
        )
    elif q:
        # Дефолтный путь: ILIKE по given/surname/concat.
        # Pattern с escape'ом ILIKE-метасимволов: % и _ внутри пользовательского
        # ввода не должны работать как wildcard'ы, иначе `q='%'` вернёт всё.
        # Backslash-escape работает в Postgres ILIKE без явного ESCAPE clause.
        safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{safe}%"
        item_match_type = "substring"
        # Подзапрос: персоны, у которых хоть одно имя удовлетворяет ILIKE.
        # Конкатенация given+surname обрабатывает запросы вида "John Smith".
        name_match = exists(
            select(Name.id).where(
                Name.person_id == Person.id,
                or_(
                    Name.given_name.ilike(pattern),
                    Name.surname.ilike(pattern),
                    func.concat(
                        func.coalesce(Name.given_name, ""),
                        " ",
                        func.coalesce(Name.surname, ""),
                    ).ilike(pattern),
                ),
            )
        )
        base_filters.append(name_match)

    if birth_year_min is not None or birth_year_max is not None:
        # Explicit ColumnElement[bool] на list — иначе mypy сужает тип
        # до BinaryExpression[bool] от первого элемента и не принимает
        # func.extract(...) сравнения (ColumnElement[bool]).
        date_filters: list[ColumnElement[bool]] = [Event.date_start.is_not(None)]
        if birth_year_min is not None:
            date_filters.append(func.extract("year", Event.date_start) >= birth_year_min)
        if birth_year_max is not None:
            date_filters.append(func.extract("year", Event.date_start) <= birth_year_max)
        birth_match = exists(
            select(Event.id)
            .join(EventParticipant, EventParticipant.event_id == Event.id)
            .where(
                EventParticipant.person_id == Person.id,
                Event.event_type == "BIRT",
                Event.deleted_at.is_(None),
                and_(*date_filters),
            )
        )
        base_filters.append(birth_match)

    total = await session.scalar(select(func.count(Person.id)).where(*base_filters))

    res = await session.execute(
        select(Person)
        .options(selectinload(Person.names))
        .where(*base_filters)
        .order_by(Person.created_at)
        .limit(limit)
        .offset(offset)
    )
    persons = res.scalars().all()

    items: list[PersonSummary] = []
    for p in persons:
        primary = next(
            (
                f"{n.given_name or ''} {n.surname or ''}".strip()
                for n in sorted(p.names, key=lambda n: n.sort_order)
                if n.given_name or n.surname
            ),
            None,
        )
        items.append(
            PersonSummary(
                id=p.id,
                gedcom_xref=p.gedcom_xref,
                sex=p.sex,
                confidence_score=p.confidence_score,
                primary_name=primary,
                match_type=item_match_type,
            )
        )
    return PersonListResponse(
        tree_id=tree_id,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/persons/{person_id}",
    response_model=PersonDetail,
    summary="Person details: names + events",
)
async def get_person(
    person_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PersonDetail:
    """Returns person with names and events."""
    res = await session.execute(
        select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
    )
    person = res.scalar_one_or_none()
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person {person_id} not found",
        )

    names_res = await session.execute(
        select(Name).where(Name.person_id == person_id).order_by(Name.sort_order)
    )
    names = [NameSummary.model_validate(n) for n in names_res.scalars().all()]

    events_res = await session.execute(
        select(Event)
        .options(joinedload(Event.place))
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .where(EventParticipant.person_id == person_id, Event.deleted_at.is_(None))
        .order_by(Event.date_start.nulls_last())
    )
    event_orms = events_res.scalars().all()
    event_ids = [e.id for e in event_orms]

    # Citations для всех event'ов одним запросом — JOIN на sources, чтобы
    # отдать source_title без второго round-trip фронта.
    citations_by_event: dict[uuid.UUID, list[CitationSummary]] = {eid: [] for eid in event_ids}
    if event_ids:
        cit_res = await session.execute(
            select(Citation, Source.title)
            .join(Source, Source.id == Citation.source_id)
            .where(
                Citation.entity_type == "event",
                Citation.entity_id.in_(event_ids),
                Citation.deleted_at.is_(None),
            )
        )
        for citation, source_title in cit_res.all():
            citations_by_event[citation.entity_id].append(
                CitationSummary(
                    source_id=citation.source_id,
                    source_title=source_title,
                    page=citation.page_or_section,
                    quality=citation.quality,
                )
            )

    events: list[EventSummary] = []
    for e in event_orms:
        summary = EventSummary.model_validate(e)
        summary.citations = citations_by_event.get(e.id, [])
        events.append(summary)

    # Multimedia персоны: JOIN entity_multimedia → multimedia_objects.
    media_res = await session.execute(
        select(MultimediaObject)
        .join(EntityMultimedia, EntityMultimedia.multimedia_id == MultimediaObject.id)
        .where(
            EntityMultimedia.entity_type == "person",
            EntityMultimedia.entity_id == person_id,
            MultimediaObject.deleted_at.is_(None),
        )
    )
    media_objs = media_res.scalars().all()
    media: list[MultimediaSummary] = []
    for obj in media_objs:
        format_ = obj.object_metadata.get("format") if obj.object_metadata else None
        media.append(
            MultimediaSummary(
                id=obj.id,
                title=obj.caption,
                file_path=obj.storage_url,
                format=format_,
            )
        )

    return PersonDetail(
        id=person.id,
        tree_id=person.tree_id,
        gedcom_xref=person.gedcom_xref,
        sex=person.sex,
        status=person.status,
        confidence_score=person.confidence_score,
        names=names,
        events=events,
        media=media,
    )


_MAX_GENERATIONS = 10


def _primary_name(names: list[Name]) -> str | None:
    """Собрать ``primary_name`` из имён, отсортированных по ``sort_order``."""
    for name in sorted(names, key=lambda n: n.sort_order):
        composed = f"{name.given_name or ''} {name.surname or ''}".strip()
        if composed:
            return composed
    return None


@router.get(
    "/persons/{person_id}/ancestors",
    response_model=AncestorsResponse,
    summary="Pedigree-дерево предков (BFS на N поколений)",
)
async def get_ancestors(
    person_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    generations: int = Query(
        5,
        ge=1,
        le=_MAX_GENERATIONS,
        description=(
            "Сколько поколений предков загружать (root считается 0-м). "
            f"Максимум {_MAX_GENERATIONS}: 2^N узлов растёт быстро."
        ),
    ),
) -> AncestorsResponse:
    """Возвращает рекурсивный pedigree корневой персоны.

    Алгоритм — BFS по поколениям:

    1. Резолвим root (404 если не найден).
    2. На каждом уровне берём ``family_children`` для текущего множества и
       по ``families.husband_id``/``wife_id`` собираем родителей. Один
       round-trip per generation вместо N+1 — детерминированно по
       ``FamilyChild.created_at`` берём первую семью на ребёнка
       (для MVP; mixed-parentage / step-parents — Phase 4.5).
    3. Параллельно ведём ``visited``-сет, чтобы зацикленные данные
       (corruption) не привели к бесконечному рекурсу.
    4. Один батч-запрос подгружает Person + Names для всех найденных
       UUID; ещё один — BIRT/DEAT-события для извлечения годов.
    5. Собираем дерево рекурсивно из словарей в памяти.
    """
    root_person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if root_person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person {person_id} not found",
        )

    # parent_map: child_id -> (father_id|None, mother_id|None)
    parent_map: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]] = {}
    visited: set[uuid.UUID] = {person_id}
    current_level: set[uuid.UUID] = {person_id}
    generations_loaded = 0

    for _ in range(generations):
        if not current_level:
            break
        rows = (
            await session.execute(
                select(
                    FamilyChild.child_person_id,
                    FamilyChild.created_at,
                    Family.husband_id,
                    Family.wife_id,
                )
                .join(Family, Family.id == FamilyChild.family_id)
                .where(FamilyChild.child_person_id.in_(current_level))
                .order_by(FamilyChild.child_person_id, FamilyChild.created_at)
            )
        ).all()

        # На ребёнка — первая встреченная семья (детерминированно
        # по created_at). Альтернативные родители — Phase 4.5.
        next_level: set[uuid.UUID] = set()
        for child_id, _created_at, husband_id, wife_id in rows:
            if child_id in parent_map:
                continue
            parent_map[child_id] = (husband_id, wife_id)
            for parent_id in (husband_id, wife_id):
                if parent_id is not None and parent_id not in visited:
                    visited.add(parent_id)
                    next_level.add(parent_id)

        if next_level:
            generations_loaded += 1
        current_level = next_level

    # Батч-загрузка Person + Names всех собранных UUID одним round-trip.
    persons_res = await session.execute(
        select(Person)
        .options(selectinload(Person.names))
        .where(Person.id.in_(visited), Person.deleted_at.is_(None))
    )
    persons_by_id: dict[uuid.UUID, Person] = {p.id: p for p in persons_res.scalars().all()}

    # Годы рождения / смерти — из BIRT / DEAT (date_start.year).
    birth_year: dict[uuid.UUID, int | None] = {}
    death_year: dict[uuid.UUID, int | None] = {}
    if visited:
        events_res = await session.execute(
            select(EventParticipant.person_id, Event.event_type, Event.date_start)
            .join(Event, Event.id == EventParticipant.event_id)
            .where(
                EventParticipant.person_id.in_(visited),
                Event.event_type.in_(("BIRT", "DEAT")),
                Event.deleted_at.is_(None),
            )
        )
        for pid, event_type, date_start in events_res.all():
            year = date_start.year if date_start is not None else None
            if event_type == "BIRT" and year is not None:
                birth_year[pid] = year
            elif event_type == "DEAT" and year is not None:
                death_year[pid] = year

    def _build(node_id: uuid.UUID | None) -> AncestorTreeNode | None:
        """Собрать ``AncestorTreeNode`` из in-memory словарей."""
        if node_id is None:
            return None
        person = persons_by_id.get(node_id)
        if person is None:
            return None
        father_id, mother_id = parent_map.get(node_id, (None, None))
        return AncestorTreeNode(
            id=person.id,
            primary_name=_primary_name(list(person.names)),
            birth_year=birth_year.get(node_id),
            death_year=death_year.get(node_id),
            sex=person.sex,
            # Phase 4.3 stub: всегда False. Phase 6 заменит на реальный lookup
            # по подтверждённым DNA-китам, привязанным к персоне.
            dna_tested=False,
            father=_build(father_id),
            mother=_build(mother_id),
        )

    root = _build(person_id)
    # Root существует — мы его выше резолвили, persons_by_id точно содержит его.
    assert root is not None

    return AncestorsResponse(
        person_id=person_id,
        generations_requested=generations,
        generations_loaded=generations_loaded,
        root=root,
    )


# -----------------------------------------------------------------------------
# Phase 6.5 — Tree statistics dashboard (read-only aggregation, ADR-0051).
# -----------------------------------------------------------------------------


# Hard cap для recursive CTE: реальные деревья редко глубже 30 поколений (≈900 лет).
# 50 — sane bound, защищает от циклов в кривых GED-данных.
_MAX_PEDIGREE_DEPTH = 50

# Recursive CTE: длина самой длинной цепочки родитель→ребёнок.
# Уровень 1 — «корни» (персоны не являющиеся children ни в одной family того же дерева).
# Уровень N+1 — дети персон уровня N через families (husband_id ИЛИ wife_id).
# Игнорирует soft-deleted families и persons. Hard-cap'нут параметром :max_depth.
_PEDIGREE_DEPTH_SQL = text("""
WITH RECURSIVE generations AS (
    SELECT p.id AS person_id, 1 AS depth
    FROM persons p
    WHERE p.tree_id = :tree_id
      AND p.deleted_at IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM family_children fc
          JOIN families f ON f.id = fc.family_id
          WHERE fc.child_person_id = p.id
            AND f.tree_id = :tree_id
            AND f.deleted_at IS NULL
      )
    UNION ALL
    SELECT fc.child_person_id AS person_id, g.depth + 1
    FROM generations g
    JOIN families f
      ON (f.husband_id = g.person_id OR f.wife_id = g.person_id)
     AND f.tree_id = :tree_id
     AND f.deleted_at IS NULL
    JOIN family_children fc ON fc.family_id = f.id
    WHERE g.depth < :max_depth
)
SELECT COALESCE(MAX(depth), 0) AS max_depth FROM generations
""")


@router.get(
    "/trees/{tree_id}/statistics",
    response_model=TreeStatisticsResponse,
    summary="Aggregated tree statistics for dashboard (Phase 6.5).",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_tree_statistics(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TreeStatisticsResponse:
    """Один read-only round-trip с агрегатами по дереву.

    Все counts фильтруют ``deleted_at IS NULL``. Не кэшируется (ADR-0051):
    объёмы данных staging'а и B2C-юзеров не оправдывают invalidation
    complexity. Если станет узким местом — кэш с TTL=60s в Redis.
    """
    # Tree существует?
    tree_exists = await session.scalar(
        select(func.count(Tree.id)).where(Tree.id == tree_id, Tree.deleted_at.is_(None))
    )
    if not tree_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {tree_id} not found",
        )

    # 7 параллельных count'ов через одну транзакцию. Каждый — простой
    # COUNT с tree_id-фильтром. SQLAlchemy serializes их через одно
    # соединение, но это всё равно 7 round-trip'ов к БД. Для staging-
    # объёмов (≤100k персон/дерево) это <50ms суммарно.
    persons_count = await session.scalar(
        select(func.count(Person.id)).where(Person.tree_id == tree_id, Person.deleted_at.is_(None))
    )
    families_count = await session.scalar(
        select(func.count(Family.id)).where(Family.tree_id == tree_id, Family.deleted_at.is_(None))
    )
    events_count = await session.scalar(
        select(func.count(Event.id)).where(Event.tree_id == tree_id, Event.deleted_at.is_(None))
    )
    sources_count = await session.scalar(
        select(func.count(Source.id)).where(Source.tree_id == tree_id, Source.deleted_at.is_(None))
    )
    hypotheses_count = await session.scalar(
        select(func.count(Hypothesis.id)).where(
            Hypothesis.tree_id == tree_id, Hypothesis.deleted_at.is_(None)
        )
    )
    dna_matches_count = await session.scalar(
        select(func.count(DnaMatch.id)).where(
            DnaMatch.tree_id == tree_id, DnaMatch.deleted_at.is_(None)
        )
    )
    places_count = await session.scalar(
        select(func.count(Place.id)).where(Place.tree_id == tree_id, Place.deleted_at.is_(None))
    )

    # Oldest birth year — MIN(date_start) среди BIRT-events этого дерева.
    # date_start — Date type, .year даёт integer. None если нет BIRT с датой.
    oldest_birth = await session.scalar(
        select(func.min(Event.date_start)).where(
            Event.tree_id == tree_id,
            Event.deleted_at.is_(None),
            Event.event_type == "BIRT",
            Event.date_start.is_not(None),
        )
    )
    oldest_birth_year = oldest_birth.year if oldest_birth is not None else None

    # Top-10 surnames через GROUP BY на names + JOIN на persons.
    # Игнорируем NULL и пустые строки. distinct(person_id) защищает от
    # double-counting если у персоны несколько Name-row с одинаковой surname.
    top_surnames_res = await session.execute(
        select(
            Name.surname,
            func.count(func.distinct(Name.person_id)).label("person_count"),
        )
        .join(Person, Person.id == Name.person_id)
        .where(
            Person.tree_id == tree_id,
            Person.deleted_at.is_(None),
            Name.deleted_at.is_(None),
            Name.surname.is_not(None),
            Name.surname != "",
        )
        .group_by(Name.surname)
        .order_by(func.count(func.distinct(Name.person_id)).desc(), Name.surname)
        .limit(10)
    )
    top_surnames = [
        TopSurname(surname=row.surname, person_count=int(row.person_count))
        for row in top_surnames_res.all()
    ]

    # Pedigree max depth — recursive CTE. Возвращает 0 если в дереве
    # нет ни одной семьи с детьми (или вообще пусто).
    depth_res = await session.execute(
        _PEDIGREE_DEPTH_SQL,
        {"tree_id": tree_id, "max_depth": _MAX_PEDIGREE_DEPTH},
    )
    pedigree_max_depth = int(depth_res.scalar() or 0)

    return TreeStatisticsResponse(
        tree_id=tree_id,
        persons_count=int(persons_count or 0),
        families_count=int(families_count or 0),
        events_count=int(events_count or 0),
        sources_count=int(sources_count or 0),
        hypotheses_count=int(hypotheses_count or 0),
        dna_matches_count=int(dna_matches_count or 0),
        places_count=int(places_count or 0),
        pedigree_max_depth=pedigree_max_depth,
        oldest_birth_year=oldest_birth_year,
        top_surnames=top_surnames,
    )
