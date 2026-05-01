"""Pydantic-модели публичного API планировщика.

Endpoint ``GET /archive-planner/persons/{person_id}/suggestions``
возвращает ``PlannerResponse``.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from archive_service.planner.catalog import DigitizationLevel


class ArchiveSuggestion(BaseModel):
    """Один предложенный архив с rationale."""

    model_config = ConfigDict(extra="forbid")

    archive_id: str = Field(description="Стабильный идентификатор архива из каталога.")
    archive_name: str = Field(description="Человеко-читаемое название архива.")
    location_country: str = Field(description="ISO-3166 alpha-2 страны архива.")
    location_city: str = Field(description="Город архива.")
    languages: list[str] = Field(description="ISO-639 языковые коды покрытия.")
    digitization_level: DigitizationLevel = Field(
        description="Уровень оцифровки: none | partial | full.",
    )
    priority_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Итоговый приоритет (0..1) — выше = релевантнее.",
    )
    reason: str = Field(
        description=(
            "Короткое объяснение, почему этот архив рекомендуется "
            "(человеко-читаемое, на en — UI локализует)."
        ),
    )
    related_event_id: uuid.UUID = Field(
        description="ID недокументированного события, для которого архив подобран.",
    )
    related_event_type: str = Field(
        description="Тип события (BIRT/DEAT/MARR/...).",
    )


class PlannerResponse(BaseModel):
    """Ответ планировщика для одной персоны."""

    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    suggestions: list[ArchiveSuggestion] = Field(
        description="Top-N архивных предложений, отсортированы по priority_score desc.",
    )
    undocumented_event_count: int = Field(
        ge=0,
        description=(
            "Сколько событий персоны без citation было найдено (≥ числу "
            "уникальных событий в suggestions; больше — если ни один архив "
            "из каталога не покрыл событие)."
        ),
    )
