"""report_bundle_jobs — bulk relationship-report jobs (Phase 24.4 / ADR-0078).

Revision ID: 0042
Revises: 0034
Create Date: 2026-05-02

Создаёт одну service-table — стейт async batch-job'ов, которые worker
``services/report-service/worker.py`` (arq) исполняет, инкрементируя
``completed_count`` / ``failed_count`` атомарно per pair и в конце
загружая ZIP-of-PDFs (или consolidated PDF) в ObjectStorage.

Без backfill — таблица новая, нет существующих row'ов.

CHECK ``total_count = jsonb_array_length(relationship_pairs)`` ловит
любое расхождение между input spec и derived total на DB-уровне —
INSERT не пройдёт, если приложение забыло проставить.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать ``report_bundle_jobs`` + индексы + CHECK constraints."""
    op.create_table(
        "report_bundle_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "output_format",
            sa.String(length=32),
            nullable=False,
            server_default="zip_of_pdfs",
        ),
        sa.Column(
            "relationship_pairs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "confidence_threshold",
            sa.Float(),
            nullable=True,
        ),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column(
            "completed_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "error_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("storage_url", sa.Text(), nullable=True),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ttl_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "total_count = jsonb_array_length(relationship_pairs)",
            name="ck_report_bundle_jobs_total_matches_pairs",
        ),
        sa.CheckConstraint(
            "completed_count >= 0 AND failed_count >= 0",
            name="ck_report_bundle_jobs_counters_non_negative",
        ),
        sa.CheckConstraint(
            "completed_count + failed_count <= total_count",
            name="ck_report_bundle_jobs_counters_within_total",
        ),
    )
    op.create_index(
        "ix_report_bundle_jobs_tree_id",
        "report_bundle_jobs",
        ["tree_id"],
    )
    op.create_index(
        "ix_report_bundle_jobs_requested_by",
        "report_bundle_jobs",
        ["requested_by"],
    )
    op.create_index(
        "ix_report_bundle_jobs_tree_status_created",
        "report_bundle_jobs",
        ["tree_id", "status", "created_at"],
    )
    op.create_index(
        "ix_report_bundle_jobs_ttl",
        "report_bundle_jobs",
        ["ttl_expires_at"],
    )


def downgrade() -> None:
    """Drop ``report_bundle_jobs`` + индексы (CHECK дропаются автоматически)."""
    op.drop_index("ix_report_bundle_jobs_ttl", table_name="report_bundle_jobs")
    op.drop_index(
        "ix_report_bundle_jobs_tree_status_created",
        table_name="report_bundle_jobs",
    )
    op.drop_index("ix_report_bundle_jobs_requested_by", table_name="report_bundle_jobs")
    op.drop_index("ix_report_bundle_jobs_tree_id", table_name="report_bundle_jobs")
    op.drop_table("report_bundle_jobs")
