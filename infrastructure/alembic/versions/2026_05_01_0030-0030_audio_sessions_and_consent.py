"""audio_sessions table + trees consent fields (Phase 10.9a / ADR-0064).

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-01

Создаёт ``audio_sessions`` (voice-to-tree session с привязанным STT-результатом)
и добавляет два поля consent в ``trees`` для per-tree privacy-gate.

``audio_sessions.consent_egress_at`` NOT NULL — критическая privacy-инварианта
(ADR-0064 §Риски, defence-in-depth поверх UI и API).

``audio_sessions.consent_egress_provider`` — VARCHAR(32) **без** CHECK-enum:
Phase 10.9.x добавит ``self-hosted-whisper`` как опцию privacy-tier; не
хочется миграции ради нового допустимого значения (см. ADR-0064 §A2).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``audio_sessions`` + add ``trees.audio_consent_*`` columns."""
    op.create_table(
        "audio_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Storage
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        # Transcription
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("transcript_provider", sa.String(64), nullable=True),
        sa.Column("transcript_model_version", sa.String(64), nullable=True),
        sa.Column(
            "transcript_cost_usd",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
        ),
        sa.Column("error_message", sa.String(2000), nullable=True),
        # Privacy gate — NOT NULL критично
        sa.Column(
            "consent_egress_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "consent_egress_provider",
            sa.String(32),
            nullable=False,
        ),
        # Mixins
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
            "updated_at",
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
            "status IN ('uploaded', 'transcribing', 'ready', 'failed')",
            name="ck_audio_sessions_status",
        ),
        sa.CheckConstraint(
            "duration_sec IS NULL OR duration_sec >= 0",
            name="ck_audio_sessions_duration_nonneg",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name="ck_audio_sessions_size_nonneg",
        ),
        sa.CheckConstraint(
            "transcript_cost_usd IS NULL OR transcript_cost_usd >= 0",
            name="ck_audio_sessions_transcript_cost_nonneg",
        ),
    )
    op.create_index(
        "ix_audio_sessions_tree_id",
        "audio_sessions",
        ["tree_id"],
    )
    op.create_index(
        "ix_audio_sessions_owner_user_id",
        "audio_sessions",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_audio_sessions_tree_id_deleted_at",
        "audio_sessions",
        ["tree_id", "deleted_at"],
    )
    op.create_index(
        "ix_audio_sessions_status",
        "audio_sessions",
        ["status"],
    )

    # Per-tree consent (ADR-0064 §B1). Nullable: NULL = consent не дан.
    # Отсутствие server_default — backfill не нужен, существующие деревья
    # должны явно opt-in через UI.
    op.add_column(
        "trees",
        sa.Column(
            "audio_consent_egress_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "trees",
        sa.Column(
            "audio_consent_egress_provider",
            sa.String(32),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop ``trees.audio_consent_*`` + ``audio_sessions``."""
    op.drop_column("trees", "audio_consent_egress_provider")
    op.drop_column("trees", "audio_consent_egress_at")

    op.drop_index("ix_audio_sessions_status", table_name="audio_sessions")
    op.drop_index(
        "ix_audio_sessions_tree_id_deleted_at",
        table_name="audio_sessions",
    )
    op.drop_index(
        "ix_audio_sessions_owner_user_id",
        table_name="audio_sessions",
    )
    op.drop_index("ix_audio_sessions_tree_id", table_name="audio_sessions")
    op.drop_table("audio_sessions")
