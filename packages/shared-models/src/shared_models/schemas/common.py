"""Общие Pydantic-блоки: timestamps, status, provenance."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shared_models.enums import EntityStatus


class SchemaBase(BaseModel):
    """База для всех DTO: from_attributes=True для удобного маппинга из ORM."""

    model_config = ConfigDict(from_attributes=True, validate_assignment=True)


class SoftTimestamps(SchemaBase):
    """Стандартные временные поля + soft-delete для read-схем."""

    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None = None


class StatusFields(SchemaBase):
    """Стандартные поля достоверности доменных записей."""

    status: EntityStatus = EntityStatus.PROBABLE
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)


class ProvenanceSchema(SchemaBase):
    """Provenance jsonb-блок (структура свободная)."""

    source_files: list[str] = Field(default_factory=list)
    import_job_id: str | None = None
    manual_edits: list[dict[str, Any]] = Field(default_factory=list)
