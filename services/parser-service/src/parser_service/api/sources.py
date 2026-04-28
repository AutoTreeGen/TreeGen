"""Sources API: список SOUR-записей дерева и evidence на карточке персоны.

Phase 3.6 — материализация эвиденс-графа.

Эндпоинты:

* ``GET /trees/{tree_id}/sources`` — пагинированный список Source.
* ``GET /sources/{source_id}`` — детали Source + все linked entities.
* ``GET /persons/{person_id}/citations`` — все citations персоны (включая
  citations её событий), с раскрытыми source_title / abbreviation,
  raw QUAY и derived confidence.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import Citation, Event, EventParticipant, Person, Source
from sqlalchemy import func, select
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
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SourceListResponse:
    """Пагинированный список ``Source`` дерева.

    Сортировка — по ``created_at`` (порядок импорта). Soft-deleted
    источники исключаются из выдачи.
    """
    total = await session.scalar(
        select(func.count(Source.id)).where(
            Source.tree_id == tree_id,
            Source.deleted_at.is_(None),
        )
    )
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
        .where(Source.tree_id == tree_id, Source.deleted_at.is_(None))
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
    linked: list[SourceLinkedEntity] = []
    for c in cit_res.scalars().all():
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
