"""import_jobs progress + cancel_requested (Phase 3.5).

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-28

Расширяет ``import_jobs`` для async-импорта через arq:

* ``progress`` (jsonb, nullable) — снапшот текущего шага worker'а
  (stage, current, total, message, ts). Также публикуется в Redis
  pubsub-канал ``job-events:{job_id}`` для live-стрима через SSE.
  Первый POST /imports создаёт row с ``progress=NULL`` —
  воркер записывает первый снапшот при переходе в стадию ``parsing``.
* ``cancel_requested`` (bool, default false) — graceful cancel-сигнал.
  Worker читает между стадиями и переводит status → cancelled.

Symmetrical с hypothesis_compute_jobs.cancel_requested (миграция 0009).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add progress (jsonb nullable) + cancel_requested (bool not null) to import_jobs."""
    op.add_column(
        "import_jobs",
        sa.Column("progress", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "import_jobs",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    """Drop the two Phase 3.5 columns."""
    op.drop_column("import_jobs", "cancel_requested")
    op.drop_column("import_jobs", "progress")
