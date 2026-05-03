"""voice_extracted_proposals table (Phase 10.9b / ADR-0075).

Revision ID: 0041
Revises: 0034
Create Date: 2026-05-02

Создаёт ``voice_extracted_proposals`` — артефакт 3-pass NLU extraction'а над
``audio_sessions.transcript_text``. Один extraction-job → N proposals,
группируются по UUID-grouper'у ``extraction_job_id`` (без отдельной job-table).

Additive: одна новая таблица + 4 индекса + 5 CHECK-constraints. Никаких
ALTER на существующих таблицах. Round-trip downgrade через ``op.drop_table``.

Базируется на 0034 (``import_jobs_validation_findings``) — текущий top
``main`` на момент re-claim'а через ``scripts/next-chain-number.ps1`` после
collision-rebump (изначально клейм был 0036, но 0034 приземлился на main +
worktrees заняли 0035-0040).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``voice_extracted_proposals`` table + indexes."""
    op.create_table(
        "voice_extracted_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "audio_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audio_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extraction_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("proposal_type", sa.String(16), nullable=False),
        sa.Column("pass_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
        ),
        sa.Column(
            "evidence_snippets",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "raw_response",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=10, scale=6),
            nullable=False,
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "proposal_type IN ('person', 'place', 'relationship', 'event', 'uncertain')",
            name="ck_voice_extracted_proposals_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_voice_extracted_proposals_status",
        ),
        sa.CheckConstraint(
            "pass_number BETWEEN 1 AND 3",
            name="ck_voice_extracted_proposals_pass_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_voice_extracted_proposals_confidence_range",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cost_usd >= 0",
            name="ck_voice_extracted_proposals_cost_nonneg",
        ),
    )
    op.create_index(
        "ix_voice_extracted_proposals_tree_id",
        "voice_extracted_proposals",
        ["tree_id"],
    )
    op.create_index(
        "ix_voice_extracted_proposals_tree_status",
        "voice_extracted_proposals",
        ["tree_id", "status"],
    )
    op.create_index(
        "ix_voice_extracted_proposals_job_id",
        "voice_extracted_proposals",
        ["extraction_job_id"],
    )
    op.create_index(
        "ix_voice_extracted_proposals_session_id",
        "voice_extracted_proposals",
        ["audio_session_id"],
    )


def downgrade() -> None:
    """Drop ``voice_extracted_proposals`` table + indexes."""
    op.drop_index(
        "ix_voice_extracted_proposals_session_id",
        table_name="voice_extracted_proposals",
    )
    op.drop_index(
        "ix_voice_extracted_proposals_job_id",
        table_name="voice_extracted_proposals",
    )
    op.drop_index(
        "ix_voice_extracted_proposals_tree_status",
        table_name="voice_extracted_proposals",
    )
    op.drop_index(
        "ix_voice_extracted_proposals_tree_id",
        table_name="voice_extracted_proposals",
    )
    op.drop_table("voice_extracted_proposals")
