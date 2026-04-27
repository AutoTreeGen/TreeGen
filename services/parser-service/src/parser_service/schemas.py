"""Pydantic-схемы для API parser-service.

DTOs из ``shared-models.schemas`` переиспользуем напрямую для read-моделей.
Здесь только response/request-schemas специфичные для HTTP-слоя.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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
