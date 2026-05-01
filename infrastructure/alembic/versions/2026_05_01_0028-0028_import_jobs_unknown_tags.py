"""Import jobs: ``unknown_tags`` jsonb (Phase 5.5a, ADR-0061).

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-01

Добавляет одну колонку — ``import_jobs.unknown_tags`` jsonb. Хранит
проприетарные / нестандартные GEDCOM-теги, которые семантический парсер
не consumes; нужно для round-trip без потерь при export'е (Ancestry
``_FSFTID``, MyHeritage ``_UID``, Geni ``_PUBLIC``, нестандартные
witnesses / godparents). См. ROADMAP §5.5.

Pattern зеркалит ``source_extractions.raw_response`` jsonb из миграции
0026 (Phase 10.2, ADR-0059). NOT NULL DEFAULT '[]' — старые import_job
rows получат пустой массив без backfill'а.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавить ``unknown_tags`` jsonb-колонку с пустым массивом по умолчанию."""
    op.add_column(
        "import_jobs",
        sa.Column(
            "unknown_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Удалить колонку ``import_jobs.unknown_tags``."""
    op.drop_column("import_jobs", "unknown_tags")
