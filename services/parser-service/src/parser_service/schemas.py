"""Pydantic-схемы для API parser-service.

DTOs из ``shared-models.schemas`` переиспользуем напрямую для read-моделей.
Здесь только response/request-schemas специфичные для HTTP-слоя.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ImportJobResponse(BaseModel):
    """Ответ на ``POST /imports`` и ``GET /imports/{id}``."""

    id: uuid.UUID
    tree_id: uuid.UUID
    status: str = Field(description="queued|processing|succeeded|failed")
    source_filename: str | None = None
    source_sha256: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PersonSummary(BaseModel):
    """Краткое представление персоны для списка."""

    id: uuid.UUID
    gedcom_xref: str | None = None
    sex: str
    confidence_score: float
    primary_name: str | None = Field(
        default=None,
        description="Первое имя из ``names`` (sort_order=0), если есть.",
    )

    model_config = ConfigDict(from_attributes=True)


class PersonListResponse(BaseModel):
    """Пагинированный список персон в дереве."""

    tree_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[PersonSummary]


class PlaceSummary(BaseModel):
    """Краткое представление места для встраивания в EventSummary."""

    id: uuid.UUID
    name: str = Field(
        validation_alias="canonical_name",
        description="Каноническое имя места (place.canonical_name).",
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class CitationSummary(BaseModel):
    """Краткая ссылка на источник для встраивания в EventSummary.

    `source_title` денормализован — берётся из join'а с `sources`,
    избавляет фронт от второго запроса.
    """

    source_id: uuid.UUID
    source_title: str
    page: str | None = None
    quality: float | None = None

    model_config = ConfigDict(from_attributes=True)


class EventSummary(BaseModel):
    """Событие персоны в карточке."""

    id: uuid.UUID
    event_type: str
    date_raw: str | None = None
    date_start: datetime | None = None
    date_end: datetime | None = None
    place_id: uuid.UUID | None = None
    place: PlaceSummary | None = None
    citations: list[CitationSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MultimediaSummary(BaseModel):
    """Краткое представление multimedia-объекта для PersonDetail.media[]."""

    id: uuid.UUID
    title: str | None = Field(
        default=None,
        validation_alias="caption",
        description="Caption медиа (MultimediaObject.caption).",
    )
    file_path: str = Field(
        validation_alias="storage_url",
        description="Путь/URL файла (MultimediaObject.storage_url).",
    )
    format: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class NameSummary(BaseModel):
    """Имя персоны в карточке."""

    id: uuid.UUID
    given_name: str | None = None
    surname: str | None = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class PersonDetail(BaseModel):
    """Детали персоны: персональные поля + связанные имена/события + media."""

    id: uuid.UUID
    tree_id: uuid.UUID
    gedcom_xref: str | None = None
    sex: str
    status: str
    confidence_score: float
    names: list[NameSummary]
    events: list[EventSummary]
    media: list[MultimediaSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AncestorTreeNode(BaseModel):
    """Узел pedigree-дерева для ``GET /persons/{id}/ancestors``.

    Рекурсивная структура: у каждой персоны опционально есть ``father``
    и ``mother`` — каждый сам же ``AncestorTreeNode``. Глубина рекурсии
    ограничена параметром ``generations`` запроса (см. trees.py).

    ``birth_year`` / ``death_year`` извлекаются из событий BIRT/DEAT
    через ``date_start.year`` (для read-only chart полной даты не нужно).
    """

    id: uuid.UUID
    primary_name: str | None = None
    birth_year: int | None = None
    death_year: int | None = None
    sex: str
    father: AncestorTreeNode | None = None
    mother: AncestorTreeNode | None = None

    model_config = ConfigDict(from_attributes=True)


# Pydantic v2 рекурсивные модели — finalize forward references.
AncestorTreeNode.model_rebuild()


class AncestorsResponse(BaseModel):
    """Обёртка для ответа ``GET /persons/{id}/ancestors``.

    Помимо корневого узла отдаём ``generations_requested`` и
    ``generations_loaded`` — фронт показывает «загружено N из запрошенных M»,
    если родительских записей в дереве меньше глубины запроса.
    """

    person_id: uuid.UUID
    generations_requested: int
    generations_loaded: int
    root: AncestorTreeNode


# -----------------------------------------------------------------------------
# Phase 3.4 — entity resolution (dedup) suggestions.
# Алгоритмы — pure functions в ``packages/entity-resolution/``;
# сервисный слой возвращает только эти DTO. См. ADR-0015.
# -----------------------------------------------------------------------------

EntityType = Literal["source", "place", "person"]


class DuplicateSuggestion(BaseModel):
    """Пара кандидатов на дедупликацию с confidence score.

    Никаких side-effects: просто read-only payload. Решение о merge —
    через UI Phase 4.5 с manual approval (CLAUDE.md §5).

    `components` — покомпонентный breakdown скорера для explainability:
    UI показывает "совпали по DM-bucket + birth_year ±1".
    `evidence` — human-readable diff (canonical names, dates etc.),
    позволяет user'у принять решение без ещё одного round-trip.
    """

    entity_type: EntityType
    entity_a_id: uuid.UUID
    entity_b_id: uuid.UUID
    confidence: float = Field(ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class DuplicateSuggestionListResponse(BaseModel):
    """Пагинированный ответ ``GET /trees/{id}/duplicate-suggestions``."""

    tree_id: uuid.UUID
    entity_type: EntityType | None = None
    min_confidence: float
    total: int
    limit: int
    offset: int
    items: list[DuplicateSuggestion]


# ---------------------------------------------------------------------------
# FamilySearch import (Phase 5.1) — см. ADR-0017
# ---------------------------------------------------------------------------


class FamilySearchImportRequest(BaseModel):
    """Параметры ``POST /imports/familysearch``.

    ``access_token`` обрабатывается **stateless**: используется только для
    одного запроса в FamilySearch API и не сохраняется ни в БД, ни в
    логах. Для traceability логируется ``sha256(access_token)[:8]``.
    """

    access_token: str = Field(
        min_length=10,
        description="OAuth access token (получает caller через PKCE flow).",
    )
    fs_person_id: str = Field(
        pattern=r"^[A-Z0-9-]+$",
        max_length=64,
        description="FamilySearch person id (например, KW7S-VQJ).",
    )
    tree_id: uuid.UUID = Field(description="ID существующего дерева в AutoTreeGen.")
    generations: int = Field(
        default=4,
        ge=1,
        le=8,
        description=(
            "Глубина pedigree (FamilySearch personal apps cap = 8). "
            "1 — только родители, 8 — максимум."
        ),
    )

    model_config = ConfigDict(extra="forbid")
