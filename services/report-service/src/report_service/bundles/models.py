"""Pydantic-схемы Bulk Bundle (Phase 24.4).

Request / response — для HTTP-слоя. Внутренние data-class'ы worker'а
держим в :mod:`report_service.bundles.runner`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from report_service.relationship.models import ClaimedRelationship

BundleStatusValue = Literal["queued", "running", "completed", "failed", "cancelled"]
BundleOutputFormatValue = Literal["zip_of_pdfs", "consolidated_pdf"]


class RelationshipPair(BaseModel):
    """Один пункт ``relationship_pairs``-массива входа.

    ``claimed_relationship`` опционален — NULL → worker auto-derive из
    Family/FamilyChild графа (см. :mod:`report_service.bundles.auto_claim`).
    Direct claims (parent_child / sibling / spouse) выводятся; cousin /
    grandparent дают 422 ещё на API-уровне с подсказкой "specify explicitly".
    """

    person_a_id: uuid.UUID
    person_b_id: uuid.UUID
    claimed_relationship: ClaimedRelationship | None = None

    model_config = ConfigDict(extra="forbid")


class BundleCreateRequest(BaseModel):
    """Тело ``POST /trees/{tree_id}/report-bundles``."""

    relationship_pairs: list[RelationshipPair] = Field(min_length=1, max_length=500)
    output_format: BundleOutputFormatValue = "zip_of_pdfs"
    confidence_threshold: float | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")


class BundleCreateResponse(BaseModel):
    """Ответ ``POST`` — 202 Accepted."""

    job_id: uuid.UUID
    total_count: int
    queued_at: dt.datetime

    model_config = ConfigDict(from_attributes=True)


class BundleErrorEntry(BaseModel):
    """Запись из ``error_summary``-jsonb."""

    pair_index: int
    person_a_id: uuid.UUID
    person_b_id: uuid.UUID
    message: str


class BundleStatusSnapshot(BaseModel):
    """Ответ ``GET /trees/{tree_id}/report-bundles/{job_id}``."""

    job_id: uuid.UUID
    tree_id: uuid.UUID
    status: BundleStatusValue
    output_format: BundleOutputFormatValue
    total_count: int
    completed_count: int
    failed_count: int
    error_summary: list[BundleErrorEntry] | None = None
    storage_url: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    ttl_expires_at: dt.datetime

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "BundleCreateRequest",
    "BundleCreateResponse",
    "BundleErrorEntry",
    "BundleOutputFormatValue",
    "BundleStatusSnapshot",
    "BundleStatusValue",
    "RelationshipPair",
]
