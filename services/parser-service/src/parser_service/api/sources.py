"""Sources API: список SOUR-записей дерева и evidence на карточке персоны.

Phase 3.6 — материализация эвиденс-графа.
Phase 4.7-finalize — `q` search на list-эндпоинте, `display_label`
denormalization на детальном эндпоинте.

Эндпоинты:

* ``GET /trees/{tree_id}/sources`` — пагинированный список Source с
  опциональным ILIKE-поиском по title/abbreviation/author.
* ``GET /sources/{source_id}`` — детали Source + все linked entities
  (display_label resolver: имя персоны, event_type+year, husband×wife).
* ``GET /persons/{person_id}/citations`` — все citations персоны (включая
  citations её событий), с раскрытыми source_title / abbreviation,
  raw QUAY и derived confidence.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import (
    Citation,
    Event,
    EventParticipant,
    Family,
    Name,
    Person,
    Source,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.schemas import (
    PersonCitationDetail,
    PersonCitationsResponse,
    SourceDetail,
    SourceLinkedEntity,
    SourceListResponse,
    SourceSummary,
)

router = APIRouter()


@router.get(
    "/trees/{tree_id}/sources",
    response_model=SourceListResponse,
    summary="Paginated list of sources in a tree",
)
async def list_sources(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[
        str | None,
        Query(
            description=(
                "Case-insensitive substring search (ILIKE) по `title`, "
                "`abbreviation`, `author`. Пусто/None — не фильтрует. "
                "Метасимволы `%` / `_` экранируются (SQL-injection-safe)."
            ),
            max_length=200,
        ),
    ] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SourceListResponse:
    """Пагинированный список ``Source`` дерева с опциональным `q` ILIKE-search.

    Сортировка — по ``created_at`` (порядок импорта). Soft-deleted
    источники исключаются из выдачи.

    `q` — substring-поиск (Phase 4.7-finalize): фильтрует по
    `title`/`abbreviation`/`author` через ILIKE с escape'ом `%` и `_`,
    чтобы пользовательский ввод не работал как wildcard. Без аргумента
    эндпоинт ведёт себя как раньше.
    """
    base_filters = [Source.tree_id == tree_id, Source.deleted_at.is_(None)]
    if q:
        # Escape ILIKE-метасимволов: `%` и `_` в пользовательском вводе
        # не должны матчить как wildcard. Backslash-escape работает в
        # Postgres ILIKE без явного ESCAPE clause.
        safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{safe}%"
        base_filters.append(
            or_(
                Source.title.ilike(pattern),
                Source.abbreviation.ilike(pattern),
                Source.author.ilike(pattern),
            )
        )

    total = await session.scalar(select(func.count(Source.id)).where(*base_filters))
    # citation_count денормализуем одним LEFT JOIN GROUP BY: цена — один
    # дополнительный COUNT-агрегат на запрос, экономия — N round-trip'ов
    # с фронта (по одному `/sources/{id}` на каждую строку списка).
    citation_count = func.count(Citation.id).label("citation_count")
    res = await session.execute(
        select(Source, citation_count)
        .outerjoin(
            Citation,
            (Citation.source_id == Source.id) & (Citation.deleted_at.is_(None)),
        )
        .where(*base_filters)
        .group_by(Source.id)
        .order_by(Source.created_at)
        .limit(limit)
        .offset(offset)
    )
    items: list[SourceSummary] = []
    for source_row, count_value in res.all():
        summary = SourceSummary.model_validate(source_row)
        summary.citation_count = int(count_value or 0)
        items.append(summary)
    return SourceListResponse(
        tree_id=tree_id,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/sources/{source_id}",
    response_model=SourceDetail,
    summary="Source details + linked entities",
)
async def get_source(
    source_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SourceDetail:
    """Детали ``Source`` плюс все entity'ы, которые на него ссылаются.

    Linked-сущности — полиморфные через ``citations`` (без FK), так что
    отдаём только ``(table, id)`` пары и ``page`` / ``quay_raw`` /
    ``quality`` каждого citation. UI сам идёт за дополнительными
    деталями каждой entity (имена / даты).
    """
    src = (
        await session.execute(
            select(Source).where(
                Source.id == source_id,
                Source.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )

    cit_res = await session.execute(
        select(Citation)
        .where(Citation.source_id == source_id, Citation.deleted_at.is_(None))
        .order_by(Citation.created_at)
    )
    citations = list(cit_res.scalars().all())

    # Один батч-запрос на каждую таблицу для display_label resolution:
    # дешевле, чем N round-trip'ов из UI за каждым linked-entity name.
    person_ids: set[uuid.UUID] = set()
    event_ids: set[uuid.UUID] = set()
    family_ids: set[uuid.UUID] = set()
    for c in citations:
        if c.entity_type == "person":
            person_ids.add(c.entity_id)
        elif c.entity_type == "event":
            event_ids.add(c.entity_id)
        elif c.entity_type == "family":
            family_ids.add(c.entity_id)
    labels = await _resolve_display_labels(
        session,
        person_ids=person_ids,
        event_ids=event_ids,
        family_ids=family_ids,
    )

    linked: list[SourceLinkedEntity] = []
    for c in citations:
        # entity_type стоит как text(32) в БД; Pydantic Literal валидирует.
        if c.entity_type not in {"person", "family", "event"}:
            # неизвестный type — пропускаем (защита от мусора в БД).
            continue
        linked.append(
            SourceLinkedEntity(
                table=c.entity_type,
                id=c.entity_id,
                page=c.page_or_section,
                quay_raw=c.quay_raw,
                quality=c.quality,
                display_label=labels.get((c.entity_type, c.entity_id)),
            )
        )

    return SourceDetail(
        id=src.id,
        tree_id=src.tree_id,
        gedcom_xref=src.gedcom_xref,
        title=src.title,
        abbreviation=src.abbreviation,
        author=src.author,
        publication=src.publication,
        repository=src.repository,
        text_excerpt=src.text_excerpt,
        source_type=src.source_type,
        linked=linked,
    )


async def _resolve_display_labels(
    session: AsyncSession,
    *,
    person_ids: set[uuid.UUID],
    event_ids: set[uuid.UUID],
    family_ids: set[uuid.UUID],
) -> dict[tuple[str, uuid.UUID], str]:
    """Резолвит human-readable labels для linked-entities source-detail'а.

    Один SELECT на каждую таблицу (а не N round-trip'ов из UI):

    * person → "given surname" из ``names`` с минимальным sort_order.
    * event → "EVENT_TYPE YEAR" (year = date_start.year, либо без года
      если date_start NULL).
    * family → "Husband × Wife" из имён husband_id / wife_id
      (либо одно из них если второй NULL).

    Возвращает dict ``(table, id) → label``. Если какой-то id не нашёл
    label'а (orphan FK / soft-deleted), его просто нет в dict — UI сам
    решит, что показать (fallback на UUID).
    """
    labels: dict[tuple[str, uuid.UUID], str] = {}

    # ---- persons: один SELECT по names с минимальным sort_order. -------------
    if person_ids:
        # DISTINCT ON (person_id) ORDER BY person_id, sort_order — Postgres-only,
        # но и так весь стек на Postgres. Берём первое имя по sort_order.
        name_rows = await session.execute(
            select(Name.person_id, Name.given_name, Name.surname)
            .where(Name.person_id.in_(person_ids), Name.deleted_at.is_(None))
            .order_by(Name.person_id, Name.sort_order)
            .distinct(Name.person_id)
        )
        for person_id, given, surname in name_rows.all():
            composed = f"{given or ''} {surname or ''}".strip()
            if composed:
                labels[("person", person_id)] = composed

    # ---- events: тип + год для лаконичности UI. ------------------------------
    if event_ids:
        evt_rows = await session.execute(
            select(Event.id, Event.event_type, Event.date_start, Event.date_raw).where(
                Event.id.in_(event_ids), Event.deleted_at.is_(None)
            )
        )
        for event_id, event_type, date_start, date_raw in evt_rows.all():
            year = date_start.year if date_start is not None else None
            if year is not None:
                label = f"{event_type} {year}"
            elif date_raw:
                # date_raw может быть длинным ("ABT 1850 (Old Style)…") — обрежем.
                short = date_raw if len(date_raw) <= 32 else f"{date_raw[:29]}…"
                label = f"{event_type} {short}"
            else:
                label = event_type
            labels[("event", event_id)] = label

    # ---- families: husband × wife (или один из двоих). -----------------------
    if family_ids:
        fam_rows = await session.execute(
            select(Family.id, Family.husband_id, Family.wife_id).where(
                Family.id.in_(family_ids), Family.deleted_at.is_(None)
            )
        )
        family_to_spouses: list[tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None]] = []
        spouse_ids: set[uuid.UUID] = set()
        for family_id, husband_id, wife_id in fam_rows.all():
            family_to_spouses.append((family_id, husband_id, wife_id))
            if husband_id:
                spouse_ids.add(husband_id)
            if wife_id:
                spouse_ids.add(wife_id)
        spouse_label: dict[uuid.UUID, str] = {}
        if spouse_ids:
            spouse_rows = await session.execute(
                select(Name.person_id, Name.given_name, Name.surname)
                .where(Name.person_id.in_(spouse_ids), Name.deleted_at.is_(None))
                .order_by(Name.person_id, Name.sort_order)
                .distinct(Name.person_id)
            )
            for person_id, given, surname in spouse_rows.all():
                composed = f"{given or ''} {surname or ''}".strip()
                if composed:
                    spouse_label[person_id] = composed
        for family_id, husband_id, wife_id in family_to_spouses:
            husband = spouse_label.get(husband_id) if husband_id else None
            wife = spouse_label.get(wife_id) if wife_id else None
            if husband and wife:
                label = f"{husband} × {wife}"
            elif husband:
                label = husband
            elif wife:
                label = wife
            else:
                continue  # совсем без имён — fallback на UUID на UI.
            labels[("family", family_id)] = label

    return labels


@router.get(
    "/persons/{person_id}/citations",
    response_model=PersonCitationsResponse,
    summary="All citations for a person (including her events)",
)
async def list_person_citations(
    person_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PersonCitationsResponse:
    """Все citations, привязанные к персоне или к её событиям.

    Объединение двух наборов:

    * ``citations`` где ``entity_type='person'`` и ``entity_id=person_id``
      (citations прямо под INDI);
    * ``citations`` где ``entity_type='event'`` и ``entity_id`` входит
      в множество event_id'ов, которые имеют persona в участниках.

    404 если персоны не существует.
    """
    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Person {person_id} not found",
        )

    # 1. Event-id'ы для events персоны.
    event_ids_res = await session.execute(
        select(Event.id)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .where(
            EventParticipant.person_id == person_id,
            Event.deleted_at.is_(None),
        )
    )
    event_ids = [row[0] for row in event_ids_res.all()]

    # 2. Citations: person-уровня + event-уровня. Один JOIN на Source
    # денормализует title/abbreviation в ответ.
    person_or_event = Citation.entity_type.in_(("person", "event"))
    matches_person = (Citation.entity_type == "person") & (Citation.entity_id == person_id)
    matches_event = (
        (Citation.entity_type == "event") & (Citation.entity_id.in_(event_ids))
        if event_ids
        else (Citation.entity_id == uuid.UUID(int=0))  # пустой OR-операнд
    )
    cit_res = await session.execute(
        select(Citation, Source.title, Source.abbreviation)
        .join(Source, Source.id == Citation.source_id)
        .where(
            person_or_event,
            matches_person | matches_event,
            Citation.deleted_at.is_(None),
        )
        .order_by(Citation.created_at)
    )

    items: list[PersonCitationDetail] = []
    for citation, source_title, source_abbreviation in cit_res.all():
        if citation.entity_type not in {"person", "family", "event"}:
            continue
        items.append(
            PersonCitationDetail(
                id=citation.id,
                source_id=citation.source_id,
                source_title=source_title,
                source_abbreviation=source_abbreviation,
                entity_type=citation.entity_type,
                entity_id=citation.entity_id,
                page=citation.page_or_section,
                quay_raw=citation.quay_raw,
                quality=citation.quality,
                event_type=citation.event_type,
                role=citation.role,
                note=citation.note,
                quoted_text=citation.quoted_text,
            )
        )

    return PersonCitationsResponse(
        person_id=person_id,
        total=len(items),
        items=items,
    )
