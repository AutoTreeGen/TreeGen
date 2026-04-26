"""Pydantic-схемы для DNA-сущностей."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import Field

from shared_models.enums import (
    DnaImportKind,
    DnaImportStatus,
    DnaPlatform,
    EthnicityPopulation,
)
from shared_models.schemas.common import SchemaBase, SoftTimestamps, StatusFields


class DnaKitBase(StatusFields):
    """Общие поля DNA-кита."""

    source_platform: DnaPlatform = DnaPlatform.ANCESTRY
    external_kit_id: str | None = None
    display_name: str | None = None
    test_date: dt.date | None = None
    ethnicity_population: EthnicityPopulation = EthnicityPopulation.GENERAL
    notes: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class DnaKitCreate(DnaKitBase):
    """Создание DNA-кита."""

    tree_id: uuid.UUID
    person_id: uuid.UUID | None = None


class DnaKitRead(DnaKitBase, SoftTimestamps):
    """Read-схема DNA-кита."""

    id: uuid.UUID
    tree_id: uuid.UUID
    owner_user_id: uuid.UUID
    person_id: uuid.UUID | None
    version_id: int


class DnaMatchRead(SoftTimestamps):
    """Read-схема одного match'а."""

    id: uuid.UUID
    tree_id: uuid.UUID
    kit_id: uuid.UUID
    external_match_id: str | None
    display_name: str | None
    total_cm: float | None
    largest_segment_cm: float | None
    segment_count: int | None
    predicted_relationship: str | None
    confidence: str | None
    shared_match_count: int | None
    matched_person_id: uuid.UUID | None
    notes: str | None
    status: str
    confidence_score: float
    version_id: int


class SharedMatchRead(SchemaBase):
    """Read-схема связи match-match."""

    id: uuid.UUID
    tree_id: uuid.UUID
    kit_id: uuid.UUID
    match_a_id: uuid.UUID
    match_b_id: uuid.UUID
    shared_cm: float | None
    source_platform: str | None
    created_at: dt.datetime


class DnaImportRead(SchemaBase):
    """Read-схема DNA-импорта (CSV-загрузки)."""

    id: uuid.UUID
    tree_id: uuid.UUID
    kit_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    source_platform: DnaPlatform
    import_kind: DnaImportKind
    source_filename: str | None
    source_size_bytes: int | None
    source_sha256: str | None
    status: DnaImportStatus
    stats: dict[str, Any]
    errors: list[dict[str, Any]]
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    created_at: dt.datetime
