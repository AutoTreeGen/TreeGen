"""FastAPI router планировщика.

Endpoint:
    ``GET /archive-planner/persons/{person_id}/suggestions``
        ?locale=ru&limit=10

Auth — через router-level ``Depends(get_current_claims)``, поднимается в
``main.py`` при ``include_router``. Здесь auth не дублируется.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from shared_models.enums import CompletenessScope
from shared_models.orm.completeness_assertion import sealed_scopes_for_person
from sqlalchemy.ext.asyncio import AsyncSession

from archive_service.database import get_session
from archive_service.planner.catalog import CatalogArchive, get_catalog
from archive_service.planner.repo import EventsFetcher, fetch_undocumented_events
from archive_service.planner.schemas import PlannerResponse
from archive_service.planner.scorer import score_archives

router = APIRouter(prefix="/archive-planner", tags=["planner"])

#: Sealed-scopes fetcher: ``person_id → frozenset[CompletenessScope]``.
#: Тип callable'а, который возвращает active sealed scope'ы. Прод-binding —
#: through ``get_sealed_scopes_fetcher``, тесты переопределяют.
SealedScopesFetcher = Callable[[uuid.UUID], Awaitable[frozenset[CompletenessScope]]]


async def get_events_fetcher(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventsFetcher:
    """Обёртка, которая упрощает override в тестах.

    В проде это закрывает session над ``fetch_undocumented_events``.
    В тестах подменяется на функцию, возвращающую синтетические DTO.
    """

    async def _fetch(person_id: uuid.UUID) -> list:  # type: ignore[type-arg]
        return await fetch_undocumented_events(session, person_id)

    return _fetch


async def get_sealed_scopes_fetcher(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SealedScopesFetcher:
    """Sealed-scopes fetcher для UI research-log annotation (Phase 15.11c).

    Зеркалит паттерн ``get_events_fetcher``: prod-binding закрывает session,
    тесты могут переопределить через ``app.dependency_overrides`` чтобы
    избежать инициализации engine'а в чисто-fetcher тестах.
    """

    async def _fetch(person_id: uuid.UUID) -> frozenset[CompletenessScope]:
        return await sealed_scopes_for_person(session, person_id)

    return _fetch


@router.get(
    "/persons/{person_id}/suggestions",
    response_model=PlannerResponse,
    summary="Suggest next archives to search for a person.",
)
async def suggest_archives(
    person_id: uuid.UUID,
    fetch_events: Annotated[EventsFetcher, Depends(get_events_fetcher)],
    catalog: Annotated[tuple[CatalogArchive, ...], Depends(get_catalog)],
    fetch_sealed_scopes: Annotated[SealedScopesFetcher, Depends(get_sealed_scopes_fetcher)],
    locale: Annotated[
        str,
        Query(
            description=(
                "User locale (ISO-639-1, e.g. 'ru' or 'pl-PL'). "
                "Влияет на ранжирование: архивы с языком пользователя "
                "получают +0.05 приоритета."
            ),
            min_length=2,
            max_length=12,
        ),
    ] = "en",
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=50,
            description="Сколько суггестий вернуть (default 10).",
        ),
    ] = 10,
) -> PlannerResponse:
    """Вернуть top-N архивных предложений для недокументированных событий персоны.

    Поведение:

    * Если у персоны нет недокументированных событий — пустой список
      ``suggestions`` и ``undocumented_event_count == 0``.
    * Если события есть, но ни один архив каталога не покрывает
      их (по country/time) — пустой список ``suggestions``,
      ``undocumented_event_count > 0`` (UI может показать "архивов нет").
    """
    events = await fetch_events(person_id)
    suggestions, undocumented_count = score_archives(
        events,
        catalog,
        locale=locale,
        limit=limit,
    )
    # Phase 15.11c (ADR-0082): annotate response с active sealed scope'ами
    # для UI research-log'а («🔒 siblings sealed — no further search»).
    sealed = await fetch_sealed_scopes(person_id)
    return PlannerResponse(
        person_id=person_id,
        suggestions=suggestions,
        undocumented_event_count=undocumented_count,
        sealed_scopes=sorted(s.value for s in sealed),
    )
