"""source_extractions + extracted_facts (Phase 10.2 / ADR-0059).

Revision ID: 0026
Revises: 0025
Create Date: 2026-04-30

Создаёт две таблицы для AI source extraction:

* ``source_extractions`` — run-level лог одного Claude-вызова, хранит
  cost (input/output tokens), prompt-/model-версии, raw_response jsonb.
* ``extracted_facts`` — per-fact suggestion (Person/Event/Relationship)
  с status pending/accepted/rejected.

Не TreeEntity: служебные таблицы, без soft-delete / provenance /
version_id (см. ADR-0059 §«Persistence shape»). Cleanup — отдельная
retention-политика в Phase 10.5+.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create source_extractions + extracted_facts."""
    op.create_table(
        "source_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "raw_response",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.String(2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_source_extractions_tree_id",
        "source_extractions",
        ["tree_id"],
    )
    op.create_index(
        "ix_source_extractions_user_created",
        "source_extractions",
        ["requested_by_user_id", "created_at"],
    )
    op.create_index(
        "ix_source_extractions_source_created",
        "source_extractions",
        ["source_id", "created_at"],
    )
    op.create_index(
        "ix_source_extractions_tree_status",
        "source_extractions",
        ["tree_id", "status"],
    )

    op.create_table(
        "extracted_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "extraction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_index", sa.Integer(), nullable=False),
        sa.Column("fact_kind", sa.String(16), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("review_note", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "fact_kind IN ('person', 'event', 'relationship')",
            name="ck_extracted_facts_fact_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_extracted_facts_status",
        ),
    )
    op.create_index(
        "ix_extracted_facts_extraction_index",
        "extracted_facts",
        ["extraction_id", "fact_index"],
    )
    op.create_index(
        "ix_extracted_facts_status",
        "extracted_facts",
        ["status"],
    )


def downgrade() -> None:
    """Drop extracted_facts + source_extractions."""
    op.drop_index("ix_extracted_facts_status", table_name="extracted_facts")
    op.drop_index(
        "ix_extracted_facts_extraction_index",
        table_name="extracted_facts",
    )
    op.drop_table("extracted_facts")

    op.drop_index(
        "ix_source_extractions_tree_status",
        table_name="source_extractions",
    )
    op.drop_index(
        "ix_source_extractions_source_created",
        table_name="source_extractions",
    )
    op.drop_index(
        "ix_source_extractions_user_created",
        table_name="source_extractions",
    )
    op.drop_index(
        "ix_source_extractions_tree_id",
        table_name="source_extractions",
    )
    op.drop_table("source_extractions")
