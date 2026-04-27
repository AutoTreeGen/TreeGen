"""Pydantic-схемы запросов и ответов dna-service.

Принципиально: никаких полей, которые могут содержать raw DNA
(genotype, rsid, position) — см. ADR-0020 §«Privacy guards».
Aggregate-only metadata.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ConsentCreate(BaseModel):
    """Запрос на создание consent record."""

    model_config = ConfigDict(extra="forbid")

    tree_id: uuid.UUID
    user_id: uuid.UUID
    kit_owner_email: EmailStr
    consent_text: str = Field(..., min_length=1)
    consent_version: str = Field(default="1.0", min_length=1, max_length=32)


class ConsentResponse(BaseModel):
    """Aggregate consent metadata (никаких decrypted DNA-данных)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tree_id: uuid.UUID
    user_id: uuid.UUID
    kit_owner_email: EmailStr
    consent_version: str
    consented_at: dt.datetime
    revoked_at: dt.datetime | None
    is_active: bool


class TestRecordResponse(BaseModel):
    """Aggregate metadata для одного загруженного blob'а."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tree_id: uuid.UUID
    consent_id: uuid.UUID
    user_id: uuid.UUID
    size_bytes: int
    sha256: str
    snp_count: int
    provider: str
    encryption_scheme: str
    uploaded_at: dt.datetime


class MatchRequest(BaseModel):
    """Запрос на matching между двумя test_record id."""

    model_config = ConfigDict(extra="forbid")

    test_a_id: uuid.UUID
    test_b_id: uuid.UUID


class MatchSegment(BaseModel):
    """Один shared segment в derived stats — без SNP-уровневых данных."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chromosome: int
    start_bp: int
    end_bp: int
    num_snps: int
    cm_length: float


class MatchRelationship(BaseModel):
    """Один relationship-кандидат с CC-BY attribution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    probability: float
    cm_range: tuple[int, int]
    source: str


class MatchResponse(BaseModel):
    """Aggregate match report для двух test_records."""

    model_config = ConfigDict(extra="forbid")

    test_a_id: uuid.UUID
    test_b_id: uuid.UUID
    test_a_provider: str
    test_b_provider: str
    test_a_snp_count: int
    test_b_snp_count: int
    shared_segments: list[MatchSegment]
    total_shared_cm: float
    longest_segment_cm: float
    relationship_predictions: list[MatchRelationship]
    warnings: list[str]
