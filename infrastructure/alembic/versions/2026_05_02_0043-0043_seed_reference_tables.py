"""Reference seed tables — Phase 22.1b / ADR-0081.

Revision ID: 0043
Revises: 0040
Create Date: 2026-05-02

Создаёт 5 service-tables для committed canonical seeds + ingested-from-
local seeds. Без backfill — таблицы новые, нет существующих row'ов.

Таблицы:

* ``country_archive_directory_seed`` — PK ``iso2``, polymorphic country
  reference (jurisdictions, archives, online DBs). v1 (14 countries) +
  v2 batches load via ``python -m seed_data ingest``.
* ``surname_variant_seed`` — composite PK ``(canonical, community_scope)``,
  455 clusters. ``variants_*`` split by script.
* ``surname_transliteration_seed`` — PK ``source_form``, 86 entries.
* ``fabrication_pattern_seed`` — PK ``pattern_id``, 61 patterns.
* ``place_lookup_seed`` — composite PK ``(old_name, modern_country)``,
  505 places. ``coordinate_precision`` distinguishes ``approximate_seed``
  (88% rows, NOT для automated geo-matching) от
  ``exact_or_high_confidence``.

Все 5 — добавлены в ``SERVICE_TABLES`` allowlist в test_schema_invariants.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
# Chain after 0040 (current alembic head: 0034 → 0042 → 0040 — note 0040
# is a child of 0042 despite the lower number, per the project's
# numbering-vs-chain split). Setting down_revision="0042" creates two
# heads (0040 and 0043 both children of 0042), which alembic refuses.
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the 5 reference seed tables."""
    # country_archive_directory_seed --------------------------------------
    op.create_table(
        "country_archive_directory_seed",
        sa.Column("iso2", sa.String(length=8), primary_key=True),
        sa.Column("country", sa.String(length=200), nullable=False),
        sa.Column("v2_batch", sa.String(length=64), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
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
    )

    # surname_variant_seed ------------------------------------------------
    op.create_table(
        "surname_variant_seed",
        sa.Column("canonical", sa.String(length=200), nullable=False),
        sa.Column("community_scope", sa.String(length=64), nullable=False),
        sa.Column("rank_within_scope", sa.Integer(), nullable=True),
        sa.Column(
            "variants_latin",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "variants_cyrillic",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "variants_hebrew",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "variants_yiddish",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
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
        sa.PrimaryKeyConstraint("canonical", "community_scope", name="pk_surname_variant_seed"),
    )

    # surname_transliteration_seed ----------------------------------------
    op.create_table(
        "surname_transliteration_seed",
        sa.Column("source_form", sa.String(length=200), primary_key=True),
        sa.Column(
            "target_forms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
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
    )

    # fabrication_pattern_seed --------------------------------------------
    op.create_table(
        "fabrication_pattern_seed",
        sa.Column("pattern_id", sa.String(length=128), primary_key=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("detection_rule", sa.Text(), nullable=False),
        sa.Column("confidence_when_flagged", sa.Float(), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
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
    )
    op.create_index(
        "ix_fabrication_pattern_seed_category",
        "fabrication_pattern_seed",
        ["category"],
    )

    # place_lookup_seed ----------------------------------------------------
    op.create_table(
        "place_lookup_seed",
        sa.Column("old_name", sa.String(length=200), nullable=False),
        sa.Column("modern_country", sa.String(length=64), nullable=False),
        sa.Column("old_name_local", sa.String(length=200), nullable=True),
        sa.Column("modern_name", sa.String(length=200), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("coordinate_precision", sa.String(length=32), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
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
        sa.PrimaryKeyConstraint("old_name", "modern_country", name="pk_place_lookup_seed"),
    )


def downgrade() -> None:
    """Drop the 5 reference seed tables."""
    op.drop_table("place_lookup_seed")
    op.drop_index(
        "ix_fabrication_pattern_seed_category",
        table_name="fabrication_pattern_seed",
    )
    op.drop_table("fabrication_pattern_seed")
    op.drop_table("surname_transliteration_seed")
    op.drop_table("surname_variant_seed")
    op.drop_table("country_archive_directory_seed")
