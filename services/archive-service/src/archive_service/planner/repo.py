"""DB-запрос: недокументированные события персоны.

Query:
1. Найти ``Person`` (заодно проверяем существование + берём ``tree_id``).
2. Все события, где персона — участник (``event_participants.person_id``),
   тип события из набора жизненных (BIRT/DEAT/MARR/BURI/CHR/BAPM), и нет
   non-soft-deleted ``Citation`` с ``entity_type='event'``.
3. LEFT JOIN ``Place`` для country_code_iso + city.

Тестируется через override ``get_session`` или прямую подмену зависимости
``get_undocumented_events_fetcher`` в conftest.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from shared_models.orm.citation import Citation
from shared_models.orm.event import Event, EventParticipant
from shared_models.orm.person import Person
from shared_models.orm.place import Place
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from archive_service.planner.dto import UndocumentedEvent

# Жизненные события, для которых имеет смысл искать архивные источники.
# CUSTOM-события исключаем (custom_type не нормализован).
_LIFE_EVENT_TYPES: Final[tuple[str, ...]] = (
    "BIRT",  # birth
    "DEAT",  # death
    "MARR",  # marriage
    "BURI",  # burial
    "CHR",  # christening
    "BAPM",  # baptism
)


async def fetch_undocumented_events(
    session: AsyncSession,
    person_id: uuid.UUID,
) -> list[UndocumentedEvent]:
    """Вернуть список жизненных событий персоны без citation.

    Возвращает пустой список, если персоны нет / soft-deleted.
    """
    person_stmt = select(Person.tree_id).where(
        Person.id == person_id,
        Person.deleted_at.is_(None),
    )
    tree_id = (await session.execute(person_stmt)).scalar_one_or_none()
    if tree_id is None:
        return []

    cited_event_ids = (
        select(Citation.entity_id)
        .where(
            Citation.entity_type == "event",
            Citation.deleted_at.is_(None),
        )
        .distinct()
        .scalar_subquery()
    )

    events_stmt = (
        select(Event, Place)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .outerjoin(Place, Place.id == Event.place_id)
        .where(
            EventParticipant.person_id == person_id,
            Event.tree_id == tree_id,
            Event.deleted_at.is_(None),
            Event.event_type.in_(_LIFE_EVENT_TYPES),
            Event.id.notin_(cited_event_ids),
        )
    )

    rows = (await session.execute(events_stmt)).all()
    return [
        UndocumentedEvent(
            event_id=event.id,
            event_type=event.event_type,
            date_start=event.date_start,
            date_end=event.date_end,
            place_country_iso=place.country_code_iso if place else None,
            place_city=(place.settlement or place.canonical_name) if place else None,
        )
        for event, place in rows
    ]


# FastAPI-friendly fetcher type — позволяет в тестах подменить целиком
# процедуру (не дёргая БД), через ``app.dependency_overrides``.
EventsFetcher = Callable[[uuid.UUID], Awaitable[list[UndocumentedEvent]]]
