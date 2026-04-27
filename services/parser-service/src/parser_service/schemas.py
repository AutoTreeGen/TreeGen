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
# Phase 3.6 — Source / Citation evidence read API.
# Поддерживает «откуда мы это знаем»-UI: отдельный source viewer (Phase 4.7)
# и citations-список на карточке персоны.
# ---------------------------------------------------------------------------


class SourceSummary(BaseModel):
    """Краткое представление SOUR-записи для списка `/trees/{id}/sources`."""

    id: uuid.UUID
    gedcom_xref: str | None = None
    title: str
    abbreviation: str | None = None
    author: str | None = None
    publication: str | None = None
    repository: str | None = None
    source_type: str

    model_config = ConfigDict(from_attributes=True)


class SourceListResponse(BaseModel):
    """Пагинированный ответ ``GET /trees/{id}/sources``."""

    tree_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[SourceSummary]


class SourceLinkedEntity(BaseModel):
    """Сущность, которая ссылается на источник через citation.

    `table` ∈ ``{"person", "family", "event"}`` (полиморфная связь
    `citations.entity_type` / `entity_id`). UI разрешает её в
    конкретный card view на стороне клиента.
    """

    table: Literal["person", "family", "event"]
    id: uuid.UUID
    page: str | None = None
    quay_raw: int | None = None
    quality: float


class SourceDetail(BaseModel):
    """Детали SOUR-записи + список linked-сущностей.

    Полный набор полей, нормализованный в Phase 3.6: TITL / AUTH / PUBL /
    ABBR / TEXT / REPO. `linked` — все entity'ы которые цитируют этот
    источник (любая комбинация person / family / event).
    """

    id: uuid.UUID
    tree_id: uuid.UUID
    gedcom_xref: str | None = None
    title: str
    abbreviation: str | None = None
    author: str | None = None
    publication: str | None = None
    repository: str | None = None
    text_excerpt: str | None = None
    source_type: str
    linked: list[SourceLinkedEntity] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class PersonCitationDetail(BaseModel):
    """Один citation на странице `/persons/{id}/citations`.

    Чем-то отличается от `CitationSummary` (используется внутри
    EventSummary): денормализован источник целиком (title + abbreviation),
    плюс QUAY raw + derived confidence + EVEN/ROLE для evidence-graph
    рендера. `entity_type` и `entity_id` показывают, к какой сущности
    привязан citation: к самой персоне или к одному из её событий.
    """

    id: uuid.UUID
    source_id: uuid.UUID
    source_title: str
    source_abbreviation: str | None = None
    entity_type: Literal["person", "family", "event"]
    entity_id: uuid.UUID
    page: str | None = None
    quay_raw: int | None = None
    quality: float
    event_type: str | None = None
    role: str | None = None
    note: str | None = None
    quoted_text: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PersonCitationsResponse(BaseModel):
    """Ответ ``GET /persons/{id}/citations``."""

    person_id: uuid.UUID
    total: int
    items: list[PersonCitationDetail]


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


# -----------------------------------------------------------------------------
# Phase 7.2 — hypothesis persistence (ADR-0021).
# Pydantic-обёртки вокруг ORM моделей Hypothesis / HypothesisEvidence
# (shared-models.orm.hypothesis). Здесь — read/write DTO для HTTP слоя.
# -----------------------------------------------------------------------------


class HypothesisEvidenceResponse(BaseModel):
    """Один evidence-row для UI explainability."""

    id: uuid.UUID
    rule_id: str
    direction: str  # "supports" | "contradicts" | "neutral"
    weight: float
    observation: str
    source_provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class HypothesisSummary(BaseModel):
    """Облегчённый Hypothesis для list-эндпоинтов (без evidences[])."""

    id: uuid.UUID
    tree_id: uuid.UUID
    hypothesis_type: str
    subject_a_type: str
    subject_a_id: uuid.UUID
    subject_b_type: str
    subject_b_id: uuid.UUID
    composite_score: float
    computed_at: datetime
    rules_version: str
    reviewed_status: str
    reviewed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class HypothesisResponse(HypothesisSummary):
    """Полный Hypothesis с evidences[] — для GET /hypotheses/{id} и POST."""

    review_note: str | None = None
    reviewed_by_user_id: uuid.UUID | None = None
    evidences: list[HypothesisEvidenceResponse] = Field(default_factory=list)


class HypothesisListResponse(BaseModel):
    """Пагинированный list для ``GET /trees/{id}/hypotheses``."""

    tree_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[HypothesisSummary]


class HypothesisCreateRequest(BaseModel):
    """``POST /trees/{tree_id}/hypotheses`` body."""

    subject_a_id: uuid.UUID
    subject_b_id: uuid.UUID
    hypothesis_type: Literal[
        "same_person",
        "parent_child",
        "siblings",
        "marriage",
        "duplicate_source",
        "duplicate_place",
    ]

    model_config = ConfigDict(extra="forbid")


class HypothesisReviewRequest(BaseModel):
    """``PATCH /hypotheses/{id}/review`` body — user judgment.

    CLAUDE.md §5: ``status='confirmed'`` НЕ автоматически мерджит entities.
    Сервис только сохраняет user-judgment + actor; merge — отдельный
    flow Phase 4.6.
    """

    status: Literal["pending", "confirmed", "rejected"]
    note: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(extra="forbid")
