"""Hypothesis + HypothesisEvidence — persistence для inference-engine (Phase 7.2).

См. ADR-0021 «Hypothesis persistence».

* ``Hypothesis`` — гипотеза о связи между двумя сущностями (например,
  «I1 и I7 — это same person»). Хранит composite_score и ссылки на
  subjects через полиморфные ``subject_*_type`` + ``subject_*_id``.
  ``reviewed_status`` — независимый трек user-judgment, не мутирует
  доменные сущности.
* ``HypothesisEvidence`` — атомарное доказательство, произведённое
  каким-то rule'ом (``rule_id`` + ``direction`` + ``weight`` +
  ``observation``). FK CASCADE на hypothesis: удаление гипотезы
  убирает её evidences автоматически.

Idempotency обеспечивается уникальным индексом
``(tree_id, hypothesis_type, subject_a_id, subject_b_id)``. Caller
обязан складывать ids в canonical order (меньшее первое) перед
INSERT, чтобы re-run для (a, b) и (b, a) попадал в одну строку.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.enums import (
    HypothesisComputedBy,
    HypothesisReviewStatus,
)
from shared_models.mixins import IdMixin, TimestampMixin, TreeEntityMixins


class Hypothesis(TreeEntityMixins, Base):
    """Гипотеза о связи между двумя сущностями + её evidence-chain.

    Зеркалирует ``inference_engine.types.Hypothesis`` (Phase 7.0), но
    persisted: имеет identity, audit-trail и review-status. Inference
    engine pure-functions компилируют этот объект из памяти, и
    ``hypothesis_runner.compute_hypothesis()`` сохраняет его сюда.
    """

    __tablename__ = "hypotheses"
    __table_args__ = (
        # Idempotency: одна гипотеза на (дерево, тип, упорядоченную пару).
        # Caller обязан передавать subject_a_id < subject_b_id (canonical).
        UniqueConstraint(
            "tree_id",
            "hypothesis_type",
            "subject_a_id",
            "subject_b_id",
            name="uq_hypotheses_tree_type_subjects",
        ),
        # composite_score в [0, 1] — guard.
        CheckConstraint(
            "composite_score >= 0 AND composite_score <= 1",
            name="ck_hypotheses_composite_score_range",
        ),
        # subject_a и subject_b должны различаться (нет смысла гипотез
        # самих с собой).
        CheckConstraint(
            "subject_a_id <> subject_b_id",
            name="ck_hypotheses_subjects_distinct",
        ),
        # Top-N hypotheses в UI: индекс по composite_score DESC внутри tree.
        Index(
            "ix_hypotheses_tree_score",
            "tree_id",
            "composite_score",
            postgresql_using="btree",
        ),
        # Все гипотезы про одну персону (для карточки subject в UI).
        Index("ix_hypotheses_subject_a", "tree_id", "subject_a_id"),
        Index("ix_hypotheses_subject_b", "tree_id", "subject_b_id"),
        # Pending-counter в navbar / status-badge.
        Index(
            "ix_hypotheses_review_status",
            "tree_id",
            "reviewed_status",
        ),
    )

    hypothesis_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    subject_a_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_b_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    composite_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0"),
    )
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    computed_by: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=HypothesisComputedBy.AUTOMATIC.value,
        server_default=HypothesisComputedBy.AUTOMATIC.value,
    )
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)

    reviewed_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=HypothesisReviewStatus.PENDING.value,
        server_default=HypothesisReviewStatus.PENDING.value,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    review_note: Mapped[str | None] = mapped_column(String, nullable=True)

    evidences: Mapped[list[HypothesisEvidence]] = relationship(
        "HypothesisEvidence",
        back_populates="hypothesis",
        cascade="all, delete-orphan",
        lazy="raise",
    )


class HypothesisEvidence(IdMixin, TimestampMixin, Base):
    """Атомарное доказательство, произведённое rule'ом для одной гипотезы.

    Соответствует ``inference_engine.types.Evidence`` 1:1. Поле
    ``source_provenance`` — JSONB-словарь с pointer'ами на reference
    data, версии алгоритмов, sha256-хэши и т.п.

    Целостность: FK CASCADE — удаление гипотезы убирает её evidences.
    """

    __tablename__ = "hypothesis_evidences"
    __table_args__ = (
        CheckConstraint(
            "weight >= 0 AND weight <= 1",
            name="ck_hyp_ev_weight_range",
        ),
        # Index для UI «покажи все гипотезы где правило X не сработало».
        Index("ix_hyp_ev_rule_id", "rule_id"),
    )

    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    observation: Mapped[str] = mapped_column(String, nullable=False)
    source_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    hypothesis: Mapped[Hypothesis] = relationship(
        "Hypothesis",
        back_populates="evidences",
    )
