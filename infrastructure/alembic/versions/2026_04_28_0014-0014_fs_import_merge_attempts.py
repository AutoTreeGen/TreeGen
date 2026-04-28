"""FsImportMergeAttempt table (Phase 5.2).

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-28

Audit-лог решений merge-mode'а FS-импорта (см. ADR-0017 §5.2 extension).
В отличие от ``fs_dedup_attempts`` (Phase 5.2.1) — это не review-queue,
а immutable журнал того, что merger выбрал *до* INSERT'а: для каждой
FS-персоны одна row с финальной стратегией (``skip`` / ``merge`` /
``create_as_new``), score'ом и pointer'ом на matched local Person'а.

CHECK constraints:

* ``ck_fs_import_merge_attempts_score_range`` — score либо NULL, либо в [0, 1].
* ``ck_fs_import_merge_attempts_strategy`` — strategy ∈ MergeStrategy.

Indexes:

* ``ix_fs_import_merge_attempts_tree_id`` (FK lookup),
* ``ix_fs_import_merge_attempts_job_id`` (lookup «какие attempts создал этот job»),
* ``ix_fs_import_merge_attempts_tree_id_fs_pid`` (cross-import audit «что
  происходило с этим fs_pid в этом дереве»).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create fs_import_merge_attempts table + indexes + constraints."""
    op.create_table(
        "fs_import_merge_attempts",
        # IdMixin + TimestampMixin (audit-style row).
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
        # Phase 5.2 specific.
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("import_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fs_pid", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("matched_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "score_components",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_fs_import_merge_attempts_tree_id_trees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name="fk_fs_import_merge_attempts_import_job_id_import_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matched_person_id"],
            ["persons.id"],
            name="fk_fs_import_merge_attempts_matched_person_id_persons",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_fs_import_merge_attempts_score_range",
        ),
        sa.CheckConstraint(
            "strategy IN ('skip', 'merge', 'create_as_new')",
            name="ck_fs_import_merge_attempts_strategy",
        ),
    )
    # FK lookup index (mirror ORM `index=True` на колонке).
    op.create_index(
        "ix_fs_import_merge_attempts_tree_id",
        "fs_import_merge_attempts",
        ["tree_id"],
    )
    # Lookup attempts конкретного job'а — для метрик и stats endpoint'а.
    op.create_index(
        "ix_fs_import_merge_attempts_job_id",
        "fs_import_merge_attempts",
        ["import_job_id"],
    )
    # Cross-import audit «что происходило с этим fs_pid в этом дереве».
    op.create_index(
        "ix_fs_import_merge_attempts_tree_id_fs_pid",
        "fs_import_merge_attempts",
        ["tree_id", "fs_pid"],
    )


def downgrade() -> None:
    """Drop fs_import_merge_attempts table + indexes."""
    op.drop_index(
        "ix_fs_import_merge_attempts_tree_id_fs_pid",
        table_name="fs_import_merge_attempts",
    )
    op.drop_index(
        "ix_fs_import_merge_attempts_job_id",
        table_name="fs_import_merge_attempts",
    )
    op.drop_index(
        "ix_fs_import_merge_attempts_tree_id",
        table_name="fs_import_merge_attempts",
    )
    op.drop_table("fs_import_merge_attempts")
