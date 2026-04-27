"""hypothesis_compute_jobs (Phase 7.5).

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-27

Adds bulk-compute job tracker для hypothesis_runner. Worker обновляет
``progress`` jsonb между batch'ами, ``cancel_requested`` читается
worker'ом для graceful shutdown.

Не TreeEntity: служебная таблица (audit-trail job'ов), без soft-delete /
provenance / version_id. Cleanup через retention-политику (Phase 7.5+).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create hypothesis_compute_jobs table."""
    op.create_table(
        "hypothesis_compute_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("rule_ids", postgresql.JSONB(), nullable=True),
        sa.Column(
            "progress",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(
                '\'{"processed": 0, "total": 0, "hypotheses_created": 0}\'::jsonb'
            ),
        ),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("error", sa.String(2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_hyp_jobs_tree_id_trees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_hyp_jobs_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_hyp_jobs_tree_id", "hypothesis_compute_jobs", ["tree_id"])
    op.create_index("ix_hyp_jobs_status", "hypothesis_compute_jobs", ["status"])
    # Idempotency lookup: last running/queued/succeeded job for a tree.
    op.create_index(
        "ix_hyp_jobs_tree_status_started",
        "hypothesis_compute_jobs",
        ["tree_id", "status", "started_at"],
    )


def downgrade() -> None:
    """Drop hypothesis_compute_jobs table."""
    op.drop_table("hypothesis_compute_jobs")
