"""DNA tables (Phase 6 MVP).

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-25

Adds tables for DNA-data ingestion:
- dna_kits         — пользовательские DNA-киты
- dna_matches      — список матчей внутри одного кита
- shared_matches   — m2m связи между matches (для кластеризации)
- dna_imports      — метаданные CSV-импортов

Не включает chromosome_segments, phased data — Week 3+.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create DNA tables."""
    # ---- dna_kits --------------------------------------------------------
    op.create_table(
        "dna_kits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_platform", sa.String(32), nullable=False, server_default="ancestry"),
        sa.Column("external_kit_id", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("test_date", sa.Date(), nullable=True),
        sa.Column("ethnicity_population", sa.String(32), nullable=False, server_default="general"),
        sa.Column("consent_signed_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
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
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_dna_kits_tree_id_trees", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_dna_kits_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"], ["persons.id"], name="fk_dna_kits_person_id_persons", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_dna_kits_tree_id", "dna_kits", ["tree_id"])
    op.create_index("ix_dna_kits_owner_user_id", "dna_kits", ["owner_user_id"])
    op.create_index("ix_dna_kits_external_kit_id", "dna_kits", ["external_kit_id"])

    # ---- dna_matches -----------------------------------------------------
    op.create_table(
        "dna_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_match_id", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("total_cm", sa.Float(), nullable=True),
        sa.Column("largest_segment_cm", sa.Float(), nullable=True),
        sa.Column("segment_count", sa.Integer(), nullable=True),
        sa.Column("predicted_relationship", sa.String(), nullable=True),
        sa.Column("confidence", sa.String(32), nullable=True),
        sa.Column("shared_match_count", sa.Integer(), nullable=True),
        sa.Column("matched_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
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
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_dna_matches_tree_id_trees", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["kit_id"], ["dna_kits.id"], name="fk_dna_matches_kit_id_dna_kits", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["matched_person_id"],
            ["persons.id"],
            name="fk_dna_matches_matched_person_id_persons",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_dna_matches_tree_id", "dna_matches", ["tree_id"])
    op.create_index("ix_dna_matches_kit_id", "dna_matches", ["kit_id"])
    op.create_index("ix_dna_matches_external_match_id", "dna_matches", ["external_match_id"])
    op.create_index("ix_dna_matches_total_cm", "dna_matches", ["total_cm"])
    op.create_index("ix_dna_matches_matched_person_id", "dna_matches", ["matched_person_id"])

    # ---- shared_matches --------------------------------------------------
    op.create_table(
        "shared_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_a_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_b_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shared_cm", sa.Float(), nullable=True),
        sa.Column("source_platform", sa.String(32), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_shared_matches_tree_id_trees", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["kit_id"],
            ["dna_kits.id"],
            name="fk_shared_matches_kit_id_dna_kits",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_a_id"],
            ["dna_matches.id"],
            name="fk_shared_matches_match_a_id_dna_matches",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_b_id"],
            ["dna_matches.id"],
            name="fk_shared_matches_match_b_id_dna_matches",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("kit_id", "match_a_id", "match_b_id", name="uq_shared_matches_triple"),
        sa.CheckConstraint("match_a_id <> match_b_id", name="ck_shared_matches_not_self"),
    )
    op.create_index("ix_shared_matches_tree_id", "shared_matches", ["tree_id"])
    op.create_index("ix_shared_matches_kit_id", "shared_matches", ["kit_id"])
    op.create_index("ix_shared_matches_match_a_id", "shared_matches", ["match_a_id"])
    op.create_index("ix_shared_matches_match_b_id", "shared_matches", ["match_b_id"])

    # ---- dna_imports -----------------------------------------------------
    op.create_table(
        "dna_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_platform", sa.String(32), nullable=False, server_default="ancestry"),
        sa.Column("import_kind", sa.String(32), nullable=False, server_default="match_list"),
        sa.Column("source_filename", sa.String(), nullable=True),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_dna_imports_tree_id_trees", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["kit_id"], ["dna_kits.id"], name="fk_dna_imports_kit_id_dna_kits", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_dna_imports_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_dna_imports_tree_id", "dna_imports", ["tree_id"])
    op.create_index("ix_dna_imports_kit_id", "dna_imports", ["kit_id"])
    op.create_index("ix_dna_imports_status", "dna_imports", ["status"])
    op.create_index("ix_dna_imports_source_sha256", "dna_imports", ["source_sha256"])


def downgrade() -> None:
    """Drop DNA tables in reverse dependency order."""
    for table in ("dna_imports", "shared_matches", "dna_matches", "dna_kits"):
        op.drop_table(table)
