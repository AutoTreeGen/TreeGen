"""ImportJob.validation_findings — structured validator findings (Phase 5.8).

Revision ID: 0034
Revises: 0033
Create Date: 2026-05-02

Добавляет JSONB-колонку ``import_jobs.validation_findings`` с
``server_default '[]'::jsonb`` — pattern зеркалит уже-существующие
``import_jobs.errors`` и ``import_jobs.unknown_tags`` (Phase 3.5 / 5.5a).

Содержимое колонки — list of JSON-сериализованных
``gedcom_parser.validator.Finding``. Findings advisory: их наличие
не блокирует import; UI / CLI читают для review.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавить ``import_jobs.validation_findings`` jsonb (default '[]')."""
    op.add_column(
        "import_jobs",
        sa.Column(
            "validation_findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Удалить колонку."""
    op.drop_column("import_jobs", "validation_findings")
