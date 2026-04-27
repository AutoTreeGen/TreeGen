"""Hypothesis + HypothesisEvidence (Phase 7.2 — ADR-0021).

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-27

Adds tables for inference-engine persistence:
- hypotheses             — гипотезы о связях между сущностями
                            (composite_score + review-status)
- hypothesis_evidences   — атомарные evidence-rows, FK CASCADE на гипотезу

Idempotency: уникальный индекс по
``(tree_id, hypothesis_type, subject_a_id, subject_b_id)``. Caller обязан
складывать ids в canonical order (меньшее первое) до INSERT.

CLAUDE.md §5: ``reviewed_status='confirmed'`` не вызывает auto-merge
доменных entities — это user-judgment, отделённый от mutation flow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create hypotheses + hypothesis_evidences tables."""
    # ---- hypotheses -------------------------------------------------------
    op.create_table(
        "hypotheses",
        # IdMixin / TreeScopedMixin / StatusMixin / ProvenanceMixin /
        # VersionedMixin / TimestampMixin / SoftDeleteMixin — всё через
        # TreeEntityMixins.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="probable",
        ),
        sa.Column(
            "confidence_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.5"),
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "version_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        # Phase 7.2 specific.
        sa.Column("hypothesis_type", sa.String(32), nullable=False),
        sa.Column("subject_a_type", sa.String(16), nullable=False),
        sa.Column("subject_a_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_b_type", sa.String(16), nullable=False),
        sa.Column("subject_b_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "composite_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "computed_by",
            sa.String(16),
            nullable=False,
            server_default="automatic",
        ),
        sa.Column("rules_version", sa.String(64), nullable=False),
        sa.Column(
            "reviewed_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_hypotheses_tree_id_trees",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name="fk_hypotheses_reviewed_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "tree_id",
            "hypothesis_type",
            "subject_a_id",
            "subject_b_id",
            name="uq_hypotheses_tree_type_subjects",
        ),
        sa.CheckConstraint(
            "composite_score >= 0 AND composite_score <= 1",
            name="ck_hypotheses_composite_score_range",
        ),
        sa.CheckConstraint(
            "subject_a_id <> subject_b_id",
            name="ck_hypotheses_subjects_distinct",
        ),
    )
    # Точечные индексы (per-attribute lookups в UI).
    op.create_index("ix_hypotheses_tree_id", "hypotheses", ["tree_id"])
    op.create_index("ix_hypotheses_hypothesis_type", "hypotheses", ["hypothesis_type"])
    op.create_index(
        "ix_hypotheses_tree_score",
        "hypotheses",
        ["tree_id", "composite_score"],
    )
    op.create_index("ix_hypotheses_subject_a", "hypotheses", ["tree_id", "subject_a_id"])
    op.create_index("ix_hypotheses_subject_b", "hypotheses", ["tree_id", "subject_b_id"])
    op.create_index(
        "ix_hypotheses_review_status",
        "hypotheses",
        ["tree_id", "reviewed_status"],
    )

    # ---- hypothesis_evidences ---------------------------------------------
    op.create_table(
        "hypothesis_evidences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("hypothesis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("observation", sa.String(), nullable=False),
        sa.Column(
            "source_provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["hypotheses.id"],
            name="fk_hyp_ev_hypothesis_id_hypotheses",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 1",
            name="ck_hyp_ev_weight_range",
        ),
    )
    op.create_index("ix_hyp_ev_hypothesis_id", "hypothesis_evidences", ["hypothesis_id"])
    op.create_index("ix_hyp_ev_rule_id", "hypothesis_evidences", ["rule_id"])


def downgrade() -> None:
    """Drop hypotheses tables in reverse FK order."""
    op.drop_table("hypothesis_evidences")
    op.drop_table("hypotheses")
