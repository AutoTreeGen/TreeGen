"""Pydantic-схемы Relationship Research Report (Phase 24.3).

Request/response — для HTTP-слоя. Внутренние ``RelationshipReportContext``
структуры — для Jinja-рендера и snapshot-тестов.

Семантика claim'а: caller утверждает, что A связан с B каким-то
``ClaimedRelationship``; report собирает evidence "за" и "против", считает
композитный confidence по 22.5-формуле и рендерит PDF.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReportLocale = Literal["en", "ru"]
ReportTitleStyle = Literal["formal", "client_friendly"]


class ClaimedRelationship(enum.StrEnum):
    """Виды relationship-claim'ов, поддержанные Phase 24.3.

    Phase 24.3 v1 даёт полное evidence-aggregation для прямых связей
    (parent_child / sibling / spouse) — они напрямую читаются из Family
    + FamilyChild + Hypothesis. Расширенные виды (cousin/grandparent/
    aunt-uncle) попадают в отчёт со специальной narrative-нотой
    "extended types: derived from chained relationships, see linked
    sub-reports" — claim принимается, но evidence-section показывает
    только DNA-evidence (если задан include_dna_evidence=True) и заявку
    на ручную верификацию. Полное chained-evidence — Phase 24.4+.
    """

    PARENT_CHILD = "parent_child"
    SIBLING = "sibling"
    GRANDPARENT_GRANDCHILD = "grandparent_grandchild"
    AUNT_UNCLE_NIECE_NEPHEW = "aunt_uncle_niece_nephew"
    FIRST_COUSIN = "first_cousin"
    SECOND_COUSIN = "second_cousin"
    THIRD_COUSIN = "third_cousin"
    FOURTH_PLUS_COUSIN = "fourth_plus_cousin"
    SPOUSE = "spouse"
    OTHER = "other"


_DIRECT_CLAIMS: frozenset[ClaimedRelationship] = frozenset(
    {
        ClaimedRelationship.PARENT_CHILD,
        ClaimedRelationship.SIBLING,
        ClaimedRelationship.SPOUSE,
    }
)


def is_direct_claim(claim: ClaimedRelationship) -> bool:
    """``True`` если claim напрямую представлен в Family/FamilyChild ORM."""
    return claim in _DIRECT_CLAIMS


class ReportOptions(BaseModel):
    """Тонкие настройки одного отчёта."""

    include_dna_evidence: bool = True
    include_archive_evidence: bool = True
    include_hypothesis_flags: bool = True
    locale: ReportLocale = "en"
    title_style: ReportTitleStyle = "formal"

    model_config = ConfigDict(extra="forbid")


class RelationshipReportRequest(BaseModel):
    """Тело ``POST /api/v1/reports/relationship``."""

    tree_id: uuid.UUID
    person_a_id: uuid.UUID
    person_b_id: uuid.UUID
    claimed_relationship: ClaimedRelationship
    options: ReportOptions = Field(default_factory=ReportOptions)

    model_config = ConfigDict(extra="forbid")


class RelationshipReportResponse(BaseModel):
    """Ответ endpoint'а — UUID отчёта + signed download URL + сводка."""

    report_id: uuid.UUID
    pdf_url: str
    expires_at: dt.datetime
    confidence: float
    evidence_count: int
    counter_evidence_count: int

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Internal report context — передаётся в Jinja.
# ---------------------------------------------------------------------------


EvidenceSeverity = Literal["supporting", "contradicting", "neutral"]
EvidenceKind = Literal[
    "citation",
    "hypothesis_evidence",
    "dna_match",
    "off_catalog_evidence",
    "inference_rule",
]


class CitationRef(BaseModel):
    """Источник, на который опирается evidence-piece. Footnote-индекс — в render."""

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


class ProvenanceSummary(BaseModel):
    """22.5-style chain-of-custody метаданные одного evidence-piece.

    Для citations / hypothesis evidence — None (Phase 22.5 ввёл provenance
    только для off-catalog Evidence rows; legacy citations остаются с
    free-form provenance JSONB через ``ProvenanceMixin``, мы туда не лезем).
    """

    channel: str
    cost_usd: float | None = None
    jurisdiction: str | None = None
    archive_name: str | None = None
    request_reference: str | None = None
    notes: str | None = None
    migrated: bool = False


class EvidencePiece(BaseModel):
    """Одна "строка" evidence-trail для отчёта.

    Может быть прямой citation, hypothesis-evidence, off-catalog 22.5
    Evidence-row, либо синтетическая DNA-piece. Severity определяет, в
    какую секцию PDF попадает (supporting / counter-evidence).
    """

    kind: EvidenceKind
    severity: EvidenceSeverity
    title: str
    description: str | None = None
    weight: float = Field(
        ge=0,
        description=(
            "22.5 weight для off-catalog evidence (1..3). Для citations — quality "
            "(0..1). Для DNA — сила сегментного match'а в [0,1]. Используется "
            "в confidence aggregation (см. confidence.py)."
        ),
    )
    match_certainty: float = Field(
        ge=0,
        le=1,
        description=(
            "Сила привязки evidence к именно этому claim'у (0..1). 1.0 для "
            "прямой citation на Family-row; ниже — для transitive evidence."
        ),
    )
    citations: list[CitationRef] = Field(default_factory=list)
    provenance: ProvenanceSummary | None = None


class PersonSummary(BaseModel):
    """Vital-stats шапки отчёта (одна персона)."""

    person_id: uuid.UUID
    primary_name: str
    aka_names: list[str] = Field(default_factory=list)
    sex: str
    birth_year: int | None = None
    death_year: int | None = None


class RelationshipReportContext(BaseModel):
    """Полный контекст для Jinja-рендера relationship-report.

    Footnote-индексы (1, 2, ...) присваиваются рендером линейно после
    объединения citations всех ``evidence`` + ``counter_evidence``.
    """

    report_id: uuid.UUID
    generated_at: dt.datetime
    locale: ReportLocale
    title_style: ReportTitleStyle

    tree_id: uuid.UUID
    tree_name: str

    person_a: PersonSummary
    person_b: PersonSummary
    claimed_relationship: ClaimedRelationship

    is_direct_claim: bool
    direct_relationship_resolved: bool = Field(
        description=(
            "Для direct-claim'ов: True если связь была найдена в "
            "Family/FamilyChild. Если False — claim есть, evidence нет; "
            "secondary verification through DNA / archives рекомендуется."
        ),
    )

    narrative: str = Field(
        description="Связный текст отчёта (4–8 параграфов), детерминированно "
        "построенный по claim_type + evidence_count + confidence."
    )

    evidence: list[EvidencePiece] = Field(default_factory=list)
    counter_evidence: list[EvidencePiece] = Field(default_factory=list)

    confidence: float = Field(
        ge=0,
        description=(
            "Композитный confidence в [0, ~3]. Формула 22.5: "
            "Σ(weight × match_certainty) для supporting минус "
            "Σ(weight × match_certainty) для contradicting, нормализованный "
            "по числу evidence-streams. См. confidence.py."
        ),
    )
    confidence_method: Literal["bayesian_22_5", "naive_count", "asserted_only"]

    methodology_statement: str
    researcher_name: str | None = None


__all__ = [
    "CitationRef",
    "ClaimedRelationship",
    "EvidenceKind",
    "EvidencePiece",
    "EvidenceSeverity",
    "PersonSummary",
    "ProvenanceSummary",
    "RelationshipReportContext",
    "RelationshipReportRequest",
    "RelationshipReportResponse",
    "ReportLocale",
    "ReportOptions",
    "ReportTitleStyle",
    "is_direct_claim",
]
