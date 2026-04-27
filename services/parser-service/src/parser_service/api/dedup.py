"""Duplicate-suggestions API (Phase 3.4 Task 5).

Эндпоинт ``GET /trees/{tree_id}/duplicate-suggestions`` — фасад над
``services.dedup_finder``. Никаких mutations: возвращает только
``DuplicateSuggestion`` пары для UI Phase 4.5 (manual approval merge).

См. ADR-0015 §«Решение» — Confidence levels: ≥0.95 / 0.80–0.95 /
0.60–0.80 / <0.60. Default ``min_confidence`` 0.80 (likely + verify).
"""

from __future__ import annotations

import uuid
from typing import Annotated, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.schemas import (
    DuplicateSuggestion,
    DuplicateSuggestionListResponse,
    EntityType,
)
from parser_service.services.dedup_finder import (
    find_person_duplicates,
    find_place_duplicates,
    find_source_duplicates,
)

router = APIRouter()

# Доступные значения для query-параметра entity_type — берём из Literal,
# чтобы держать единый источник правды с DuplicateSuggestion схемой.
_ENTITY_TYPE_VALUES = frozenset(get_args(EntityType))


@router.get(
    "/trees/{tree_id}/duplicate-suggestions",
    response_model=DuplicateSuggestionListResponse,
    tags=["dedup"],
    summary="Suggest entity duplicates within a tree (read-only).",
    description=(
        "Возвращает пары кандидатов на дедупликацию (sources / places / "
        "persons) с confidence score. **Никакого автомата merge** — это "
        "только suggestions, финальное решение принимает user через UI "
        "Phase 4.5 (см. ADR-0015 + CLAUDE.md §5)."
    ),
)
async def list_duplicate_suggestions(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    entity_type: EntityType | None = Query(
        default=None,
        description=(
            "Фильтр по типу сущности. Если не задан — возвращаются все "
            "три категории (sources, places, persons)."
        ),
    ),
    min_confidence: float = Query(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Минимальный confidence score (см. ADR-0015 levels).",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> DuplicateSuggestionListResponse:
    """Запустить dedup-scoring и вернуть paginated suggestions."""
    if entity_type is not None and entity_type not in _ENTITY_TYPE_VALUES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown entity_type: {entity_type}",
        )

    suggestions: list[DuplicateSuggestion] = []
    if entity_type in (None, "source"):
        suggestions.extend(await find_source_duplicates(session, tree_id, threshold=min_confidence))
    if entity_type in (None, "place"):
        suggestions.extend(await find_place_duplicates(session, tree_id, threshold=min_confidence))
    if entity_type in (None, "person"):
        suggestions.extend(await find_person_duplicates(session, tree_id, threshold=min_confidence))

    # Глобальная сортировка по confidence DESC чтобы сильные кандидаты
    # шли первыми (внутри одной категории dedup_finder уже сортирует).
    suggestions.sort(key=lambda s: s.confidence, reverse=True)

    total = len(suggestions)
    page = suggestions[offset : offset + limit]
    return DuplicateSuggestionListResponse(
        tree_id=tree_id,
        entity_type=entity_type,
        min_confidence=min_confidence,
        total=total,
        limit=limit,
        offset=offset,
        items=page,
    )
