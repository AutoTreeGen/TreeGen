"""Pydantic-схемы Court-Ready Report (Phase 15.6).

Request / response — для HTTP-слоя; внутренние ``ReportContext`` структуры
— для Jinja-рендера и снапшот-тестов.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReportScope = Literal["person", "family", "ancestry_to_gen"]
ReportLocale = Literal["en", "ru"]


class CourtReadyReportRequest(BaseModel):
    """Тело ``POST /api/v1/reports/court-ready``."""

    person_id: uuid.UUID
    scope: ReportScope = "person"
    target_gen: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description=(
            "Глубина для scope='ancestry_to_gen' (1 = родители, 2 = бабушки/дедушки, ...). "
            "Игнорируется для других scope'ов."
        ),
    )
    locale: ReportLocale = "en"

    model_config = ConfigDict(extra="forbid")


class CourtReadyReportResponse(BaseModel):
    """Ответ endpoint'а — UUID отчёта + signed download URL + expiry."""

    report_id: uuid.UUID
    pdf_url: str
    expires_at: dt.datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Internal report context (передаётся в Jinja).
# ---------------------------------------------------------------------------


class CitationRef(BaseModel):
    """Источник, на который опирается claim. Footnote-индекс присваивается рендером."""

    source_id: uuid.UUID
    citation_id: uuid.UUID
    source_title: str
    author: str | None = None
    publication: str | None = None
    publication_date: dt.date | None = None
    repository: str | None = None
    url: str | None = None
    page_or_section: str | None = None
    quoted_text: str | None = None
    quality: float
    quay_raw: int | None = None


class EventClaim(BaseModel):
    """Одно событие персоны/семьи в evidence trail.

    ``citations`` пуст → claim попадает в Negative findings (event-without-source).
    """

    event_id: uuid.UUID
    event_type: str
    custom_type: str | None = None
    date_raw: str | None = None
    date_start: dt.date | None = None
    date_end: dt.date | None = None
    place_name: str | None = None
    description: str | None = None
    citations: list[CitationRef] = Field(default_factory=list)


class RelationshipClaim(BaseModel):
    """Связь с другим persons + evidence-rollup для неё.

    ``confidence_score`` ≤ 0 либо ``confidence_method='naive_count'`` без
    citations — повод подсветить связь как weakly-attested в отчёте.
    """

    relation_kind: Literal["parent", "child", "spouse", "sibling"]
    other_person_id: uuid.UUID
    other_person_name: str
    evidence_type: Literal["citation", "inference_rule", "asserted_only"]
    confidence_score: float
    confidence_method: Literal["bayesian_fusion_v2", "naive_count", "asserted_only"]
    citations: list[CitationRef] = Field(default_factory=list)


class SubjectSummary(BaseModel):
    """Vital stats шапки отчёта."""

    person_id: uuid.UUID
    primary_name: str
    aka_names: list[str] = Field(default_factory=list)
    sex: str
    birth: EventClaim | None = None
    death: EventClaim | None = None


class NegativeFinding(BaseModel):
    """Записи о gaps: event без citation, относительность без evidence, soft-deleted refs.

    UI-side не нужен, чисто для PDF-секции «Negative findings».
    """

    kind: Literal["event_without_source", "relationship_without_evidence", "missing_vital"]
    description: str
    related_event_id: uuid.UUID | None = None
    related_person_id: uuid.UUID | None = None


class ReportContext(BaseModel):
    """Полный контекст для Jinja-рендера.

    Footnote-индексы (1, 2, ...) присваиваются рендером линейно, после
    объединения citations из subject + relationships + ancestry — поэтому
    тут только структура, без индексов.
    """

    report_id: uuid.UUID
    generated_at: dt.datetime
    locale: ReportLocale
    scope: ReportScope
    target_gen: int | None = None

    tree_id: uuid.UUID
    tree_name: str

    subject: SubjectSummary
    other_events: list[EventClaim] = Field(default_factory=list)
    relationships: list[RelationshipClaim] = Field(default_factory=list)
    ancestry: list[SubjectSummary] = Field(default_factory=list)
    negative_findings: list[NegativeFinding] = Field(default_factory=list)

    methodology_statement: str
    researcher_name: str | None = None


__all__ = [
    "CitationRef",
    "CourtReadyReportRequest",
    "CourtReadyReportResponse",
    "EventClaim",
    "NegativeFinding",
    "RelationshipClaim",
    "ReportContext",
    "ReportLocale",
    "ReportScope",
    "SubjectSummary",
]
