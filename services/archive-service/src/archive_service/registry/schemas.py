"""Pydantic-схемы для archive registry endpoints (Phase 22.1).

Read-side (``ArchiveListingRead``) — то, что отдаём клиентам.
Write-side (``ArchiveListingCreate`` / ``ArchiveListingUpdate``) — admin-CRUD.

``fee_range_usd`` в DTO представлен как tuple ``(min, max)`` per brief —
маппинг на две DB-колонки делается в репо/роутере.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator
from shared_models.orm.archive_listing import AccessMode, RecordType


class ArchiveListingBase(BaseModel):
    """Общие поля listing для read/write DTO."""

    name: Annotated[str, Field(min_length=1, max_length=255)]
    name_native: Annotated[str | None, Field(default=None, max_length=255)] = None
    country: Annotated[str, Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")]
    region: Annotated[str | None, Field(default=None, max_length=128)] = None
    address: str | None = None
    contact_email: Annotated[str | None, Field(default=None, max_length=254)] = None
    contact_phone: Annotated[str | None, Field(default=None, max_length=64)] = None
    website: Annotated[str | None, Field(default=None, max_length=512)] = None
    languages: list[str] = Field(default_factory=list)
    record_types: list[RecordType] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    access_mode: AccessMode = AccessMode.PAID_REQUEST
    fee_min_usd: Annotated[int | None, Field(default=None, ge=0)] = None
    fee_max_usd: Annotated[int | None, Field(default=None, ge=0)] = None
    typical_response_days: Annotated[int | None, Field(default=None, ge=0)] = None
    privacy_window_years: Annotated[int | None, Field(default=None, ge=0)] = None
    notes: str | None = None
    last_verified: dt.date

    @field_validator("year_to")
    @classmethod
    def _validate_year_range(cls, value: int | None, info) -> int | None:  # type: ignore[no-untyped-def]
        """Если оба года заданы — ``year_to >= year_from``."""
        year_from = info.data.get("year_from")
        if value is not None and year_from is not None and value < year_from:
            msg = "year_to must be >= year_from"
            raise ValueError(msg)
        return value

    @field_validator("fee_max_usd")
    @classmethod
    def _validate_fee_range(cls, value: int | None, info) -> int | None:  # type: ignore[no-untyped-def]
        """Если оба fee заданы — ``fee_max_usd >= fee_min_usd``."""
        fee_min = info.data.get("fee_min_usd")
        if value is not None and fee_min is not None and value < fee_min:
            msg = "fee_max_usd must be >= fee_min_usd"
            raise ValueError(msg)
        return value


class ArchiveListingCreate(ArchiveListingBase):
    """Body для POST /archives/registry."""


class ArchiveListingUpdate(BaseModel):
    """Body для PATCH /archives/registry/{id} — все поля опциональны.

    Pattern зеркалит partial-update parser-service: PATCH меняет только
    переданные поля; non-set остаются как есть.
    """

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=255)] = None
    name_native: Annotated[str | None, Field(default=None, max_length=255)] = None
    country: Annotated[
        str | None,
        Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"),
    ] = None
    region: Annotated[str | None, Field(default=None, max_length=128)] = None
    address: str | None = None
    contact_email: Annotated[str | None, Field(default=None, max_length=254)] = None
    contact_phone: Annotated[str | None, Field(default=None, max_length=64)] = None
    website: Annotated[str | None, Field(default=None, max_length=512)] = None
    languages: list[str] | None = None
    record_types: list[RecordType] | None = None
    year_from: int | None = None
    year_to: int | None = None
    access_mode: AccessMode | None = None
    fee_min_usd: Annotated[int | None, Field(default=None, ge=0)] = None
    fee_max_usd: Annotated[int | None, Field(default=None, ge=0)] = None
    typical_response_days: Annotated[int | None, Field(default=None, ge=0)] = None
    privacy_window_years: Annotated[int | None, Field(default=None, ge=0)] = None
    notes: str | None = None
    last_verified: dt.date | None = None


class ArchiveListingRead(ArchiveListingBase):
    """Response model: listing + computed fields для UI.

    ``rank_score`` присутствует только в response GET /registry (search) —
    NULL для GET /registry/{id}.

    ``privacy_blocked`` — true когда query year попал в privacy window
    (запись существует, но недоступна обычным способом).
    """

    id: uuid.UUID
    created_at: dt.datetime
    updated_at: dt.datetime
    rank_score: float | None = None
    privacy_blocked: bool = False

    model_config = ConfigDict(from_attributes=True)


class ArchiveRegistryResponse(BaseModel):
    """Wrapper для GET /archives/registry — list + total + applied filters."""

    items: list[ArchiveListingRead]
    total: int
    country: str | None = None
    record_type: RecordType | None = None
    year_from: int | None = None
    year_to: int | None = None


__all__ = [
    "ArchiveListingBase",
    "ArchiveListingCreate",
    "ArchiveListingRead",
    "ArchiveListingUpdate",
    "ArchiveRegistryResponse",
]
