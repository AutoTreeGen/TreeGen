"""DNA consents + test records (Phase 6.2 — ADR-0020).

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27

Adds tables for DNA service:
- dna_consents       — explicit consent records per kit
- dna_test_records   — metadata for encrypted DNA blobs

Soft-delete columns are intentionally absent: ADR-0012 + ADR-0020 opt
DNA out of ADR-0003 soft-delete; revocation is hard delete.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create DNA consent + test record tables."""
    # ---- dna_consents -----------------------------------------------------
    op.create_table(
        "dna_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kit_owner_email", sa.String(320), nullable=False),
        sa.Column("consent_text", sa.String(), nullable=False),
        sa.Column(
            "consent_version",
            sa.String(32),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column(
            "consented_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_dna_consents_tree_id_trees",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_dna_consents_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_dna_consents_tree_id", "dna_consents", ["tree_id"])
    op.create_index("ix_dna_consents_user_id", "dna_consents", ["user_id"])

    # ---- dna_test_records --------------------------------------------------
    op.create_table(
        "dna_test_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("snp_count", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column(
            "encryption_scheme",
            sa.String(32),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_dna_test_records_tree_id_trees",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["consent_id"],
            ["dna_consents.id"],
            name="fk_dna_test_records_consent_id_dna_consents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_dna_test_records_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_dna_test_records_tree_id", "dna_test_records", ["tree_id"])
    op.create_index("ix_dna_test_records_consent_id", "dna_test_records", ["consent_id"])
    op.create_index("ix_dna_test_records_user_id", "dna_test_records", ["user_id"])
    op.create_index("ix_dna_test_records_sha256", "dna_test_records", ["sha256"])


def downgrade() -> None:
    """Drop DNA consent + test record tables in reverse FK order."""
    op.drop_table("dna_test_records")
    op.drop_table("dna_consents")
