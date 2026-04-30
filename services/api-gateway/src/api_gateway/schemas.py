"""Pydantic-схемы api-gateway.

Phase 16.1a — proposal CRUD: входящий ``ProposalCreate`` (с валидацией
``diff`` shape) + outbound ``ProposalRead`` / ``ProposalListResponse``.

``ProtectionPolicy`` валидирует ``trees.protection_policy`` jsonb на
читателе (не на writer'е — owner мутирует policy через отдельный
endpoint Phase 16.1b).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProposalStatus = Literal["open", "approved", "rejected", "merged", "rolled_back"]
RelationshipKind = Literal["parent_child", "spouse", "sibling", "other"]


class EvidenceRequirement(BaseModel):
    """Один элемент ``evidence_required`` (auto-populated из policy).

    Caller (16.1b approve-validator) проверяет, что для каждого item-а
    есть хотя бы один ``tree_change_proposal_evidence``-row с совпадающим
    ``relationship_ref``.
    """

    relationship_id: str = Field(min_length=1)
    kind: RelationshipKind


class ProtectionPolicy(BaseModel):
    """``trees.protection_policy`` jsonb shape.

    Default-всё-пусто значит: protection включён, но никаких specific
    requirements — каждый change всё ещё идёт через PR-flow для review,
    но evidence не enforced.
    """

    require_evidence_for: list[RelationshipKind] = Field(default_factory=list)
    min_reviewers: int = Field(default=1, ge=0, le=5)
    allow_owner_bypass: bool = Field(default=False)


class ProposalDiff(BaseModel):
    """Структурированный diff одного proposal.

    Каждая запись — opaque dict caller'а (16.1c merge engine
    интерпретирует). Здесь валидируем только верхнюю shape (три ключа,
    каждый — list).
    """

    creates: list[dict[str, Any]] = Field(default_factory=list)
    updates: list[dict[str, Any]] = Field(default_factory=list)
    deletes: list[dict[str, Any]] = Field(default_factory=list)


class ProposalCreate(BaseModel):
    """Тело ``POST /trees/{id}/proposals``."""

    title: str = Field(min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=10_000)
    diff: ProposalDiff


class ProposalRead(BaseModel):
    """Outbound representation одного proposal."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tree_id: uuid.UUID
    author_user_id: uuid.UUID
    title: str
    summary: str | None
    diff: dict[str, Any]
    status: ProposalStatus
    evidence_required: list[dict[str, Any]]
    created_at: dt.datetime
    updated_at: dt.datetime
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: dt.datetime | None
    merged_at: dt.datetime | None
    merge_commit_id: uuid.UUID | None
    rolled_back_at: dt.datetime | None
    rolled_back_by_user_id: uuid.UUID | None


class ProposalListResponse(BaseModel):
    """Тело ``GET /trees/{id}/proposals``."""

    tree_id: uuid.UUID
    total: int = Field(ge=0)
    limit: int
    offset: int
    items: list[ProposalRead]


__all__ = [
    "EvidenceRequirement",
    "ProposalCreate",
    "ProposalDiff",
    "ProposalListResponse",
    "ProposalRead",
    "ProposalStatus",
    "ProtectionPolicy",
    "RelationshipKind",
]
