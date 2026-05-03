"""VoiceExtractedProposal — артефакт 3-pass NLU extraction (Phase 10.9b / ADR-0075).

Один прогон ``voice_extract_job`` над ``AudioSession.transcript_text`` создаёт
группу proposals (объединена ``extraction_job_id``); каждый proposal — одно
предложение модели на review (10.9c). НИКАКОГО write'а в ``persons`` /
``families`` / ``events`` напрямую — review queue решает; см. ADR-0075
§«Что НЕ закрыто».

Service-table pattern (mirror ``AudioSession`` / ``SourceExtraction``):

* НЕ ``TreeEntityMixins`` — это AI-артефакт, не доменная сущность.
* НЕ ``SoftDeleteMixin`` — иначе попадает под audit-listener
  (см. ``shared_models.audit._is_audited``); ``deleted_at`` объявлен напрямую.
* ``status`` — узкий lifecycle review queue (``pending|approved|rejected``),
  не доменный confidence_score / version_id.

Privacy: row живёт под FK CASCADE на ``audio_sessions``, который сам CASCADE'ит
на ``trees``. GDPR-erasure (ADR-0049) удаляет всю цепочку через application-level
worker; DB-CASCADE — safety net.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin


class ProposalType(enum.StrEnum):
    """Тип одного proposal'а из 3-pass pipeline.

    - ``PERSON`` — pass 1, ``create_person`` tool-call.
    - ``PLACE`` — pass 1, ``add_place`` tool-call.
    - ``RELATIONSHIP`` — pass 2, ``link_relationship`` tool-call.
    - ``EVENT`` — pass 3, ``add_event`` tool-call.
    - ``UNCERTAIN`` — любой pass, ``flag_uncertain`` tool-call (manual review).
    """

    PERSON = "person"
    PLACE = "place"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    UNCERTAIN = "uncertain"


class ProposalStatus(enum.StrEnum):
    """Lifecycle review queue (ADR-0075 §«Review»).

    Создаётся как ``PENDING``; 10.9c review-UI переключает на ``APPROVED``
    (тогда worker конвертирует в ``Hypothesis`` / domain-row) или ``REJECTED``
    (остаётся для audit, не конвертируется).
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExtractionJobStatus(enum.StrEnum):
    """Финальный статус одного extraction job'а (логируется в provenance).

    Хранится не в этой таблице (нет ``voice_extraction_jobs`` row),
    а в ``provenance['job_status']`` каждого proposal'а. Перечисление
    зеркалит ADR-0075 §«Failure handling».
    """

    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    COST_CAPPED = "cost_capped"
    FAILED = "failed"


class VoiceExtractedProposal(IdMixin, Base):
    """Один proposal из 3-pass NLU extraction'а одной ``AudioSession``.

    FK ``audio_session_id → audio_sessions.id ON DELETE CASCADE`` — proposals
    исчезают вместе с сессией; FK ``tree_id → trees.id ON DELETE CASCADE`` —
    safety net для прямого ``DELETE FROM trees`` (ADR-0049 erasure pipeline
    отрабатывает через application-level worker).

    ``extraction_job_id`` — UUID-grouper, не FK. Все proposals одного запуска
    разделяют его; review-queue в 10.9c group-by по этому полю.
    """

    __tablename__ = "voice_extracted_proposals"
    __table_args__ = (
        # Review-queue UI: «все pending для дерева» + «все proposals одного job'а».
        Index(
            "ix_voice_extracted_proposals_tree_status",
            "tree_id",
            "status",
        ),
        Index(
            "ix_voice_extracted_proposals_job_id",
            "extraction_job_id",
        ),
        Index(
            "ix_voice_extracted_proposals_session_id",
            "audio_session_id",
        ),
        CheckConstraint(
            "proposal_type IN ('person', 'place', 'relationship', 'event', 'uncertain')",
            name="ck_voice_extracted_proposals_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_voice_extracted_proposals_status",
        ),
        CheckConstraint(
            "pass_number BETWEEN 1 AND 3",
            name="ck_voice_extracted_proposals_pass_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_voice_extracted_proposals_confidence_range",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cost_usd >= 0",
            name="ck_voice_extracted_proposals_cost_nonneg",
        ),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    audio_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audio_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # UUID-grouper: один extraction-job = N proposals; review-queue group-by.
    extraction_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Шапка proposal'а
    proposal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    pass_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ProposalStatus.PENDING.value,
        server_default=ProposalStatus.PENDING.value,
    )

    # Tool input args (валидируется по schema на уровне use-case'а)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(precision=4, scale=3),
        nullable=False,
    )
    evidence_snippets: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    # Audit (mirror SourceExtraction.raw_response shape)
    raw_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)

    # Cost telemetry (Decimal — общая практика для Anthropic NLU cost'ов)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=6),
        nullable=False,
    )

    # Provenance (mirror service-table pattern; не через ProvenanceMixin
    # потому что мы не хотим audit-listener)
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        """Помечен ли proposal как удалённый (mirror ``SoftDeleteMixin``)."""
        return self.deleted_at is not None


__all__ = [
    "ExtractionJobStatus",
    "ProposalStatus",
    "ProposalType",
    "VoiceExtractedProposal",
]
