"""DNA match-list ingest schema additions — Phase 16.3 / ADR-0072.

Revision ID: 0037
Revises: 0036
Create Date: 2026-05-02

Расширяет существующую таблицу ``dna_matches`` (Phase 6.x) для
поддержки ingest match-list CSV из 5 платформ:

* ``platform``                            — text, denormalized из
  ``dna_kits.source_platform`` для прямой фильтрации без join'а.
* ``match_username``                      — отдельный username, когда
  платформа разделяет username и display_name (23andMe).
* ``predicted_relationship_normalized``   — bucket
  :class:`PredictedRelationship` (ADR-0072) рядом с raw-text.
* ``resolution_confidence``               — float-confidence для 16.5
  cross-platform resolver, отдельно от legacy ``confidence`` (str).
* ``raw_payload``                         — JSONB полная CSV-row,
  source of truth для re-parse при эволюции импортного парсера.

Аддитивная миграция; новые колонки nullable (кроме raw_payload —
NOT NULL DEFAULT '{}'). Существующие ряды получат пустой raw_payload
и NULL для остальных полей; backfill из существующих CSV не делаем —
данные у пользователей, повторный импорт через POST /dna/match-list/import
заполнит как нужно.

Indexes:

* ``ix_dna_matches_platform`` — partial filter поверх tree-index'а.
* ``ix_dna_matches_predicted_relationship_normalized`` — для 16.5
  cross-platform aggregation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Расширить ``dna_matches`` для match-list ingest (Phase 16.3)."""
    op.add_column(
        "dna_matches",
        sa.Column("platform", sa.String(32), nullable=True),
    )
    op.add_column(
        "dna_matches",
        sa.Column("match_username", sa.String, nullable=True),
    )
    op.add_column(
        "dna_matches",
        sa.Column(
            "predicted_relationship_normalized",
            sa.String(64),
            nullable=True,
        ),
    )
    op.add_column(
        "dna_matches",
        sa.Column("resolution_confidence", sa.Float, nullable=True),
    )
    op.add_column(
        "dna_matches",
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_index(
        "ix_dna_matches_platform",
        "dna_matches",
        ["platform"],
    )
    op.create_index(
        "ix_dna_matches_predicted_relationship_normalized",
        "dna_matches",
        ["predicted_relationship_normalized"],
    )


def downgrade() -> None:
    """Откатить Phase 16.3 column add'ы (drop indexes first)."""
    op.drop_index(
        "ix_dna_matches_predicted_relationship_normalized",
        table_name="dna_matches",
    )
    op.drop_index("ix_dna_matches_platform", table_name="dna_matches")
    op.drop_column("dna_matches", "raw_payload")
    op.drop_column("dna_matches", "resolution_confidence")
    op.drop_column("dna_matches", "predicted_relationship_normalized")
    op.drop_column("dna_matches", "match_username")
    op.drop_column("dna_matches", "platform")
