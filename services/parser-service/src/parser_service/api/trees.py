"""Trees API: list persons in a tree + person detail."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import Event, EventParticipant, Name, Person
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from parser_service.database import get_session
from parser_service.schemas import (
    EventSummary,
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
    events = [EventSummary.model_validate(e) for e in events_res.scalars().all()]

    return PersonDetail(
        id=person.id,
        tree_id=person.tree_id,
        gedcom_xref=person.gedcom_xref,
        sex=person.sex,
        status=person.status,
        confidence_score=person.confidence_score,
        names=names,
        events=events,
    )
