"""Trees API: list persons in a tree + person detail + ancestors pedigree."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import (
    Citation,
    EntityMultimedia,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    MultimediaObject,
    Name,
    Person,
    Source,
    Tree,
)
from sqlalchemy import and_, exists, func, or_, select
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
)

router = APIRouter()


@router.get(
    "/trees/{tree_id}/persons",
    response_model=PersonListResponse,
    summary="Paginated list of persons in a tree",
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
    summary="Search persons in a tree by name and birth-year range",
)
async def search_persons(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[
        str | None,
        Query(
            description=(
                "Case-insensitive substring search across given_name, surname, "
                "и их конкатенацию. ILIKE %q%. Пусто/None — не фильтрует. "
                "Параметр SQL-injection-safe (SQLAlchemy parameterizes)."
            ),
            max_length=200,
        ),
    ] = None,
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
    """Search persons by name substring + birth-year range.

    Возвращает тот же ``PersonListResponse`` что и list-эндпоинт.
    Если все три фильтра пустые — endpoint эквивалентен пагинированному
    list (полезно как унифицированный entry point из UI с lazy-фильтрами).

    Tree existence: 404 если ``tree_id`` не существует в БД (в отличие
    от list-эндпоинта, который возвращает пустой результат — search
    делает явный roundtrip и должен сообщать «такого дерева нет»).

    Birth year — опциональный фильтр через ``EXISTS`` подзапрос на
    BIRT-событие с ``date_start`` в указанном диапазоне. Персоны без
    BIRT (или без ``date_start``) исключаются когда хотя бы один из
    ``birth_year_min`` / ``birth_year_max`` задан.
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

    if q:
        # Pattern с escape'ом ILIKE-метасимволов: % и _ внутри пользовательского
        # ввода не должны работать как wildcard'ы, иначе `q='%'` вернёт всё.
        # Backslash-escape работает в Postgres ILIKE без явного ESCAPE clause.
        safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{safe}%"
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
        date_filters = [Event.date_start.is_not(None)]
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
