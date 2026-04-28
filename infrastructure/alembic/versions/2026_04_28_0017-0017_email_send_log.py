"""Email transactional log + users.email_opt_out (Phase 12.2a, ADR-0039).

Revision ID: 0017
Revises: 0015
Create Date: 2026-04-28

Создаёт ``email_send_log`` таблицу для idempotent transactional-email
dispatch'а и добавляет ``users.email_opt_out`` boolean-флаг.

Координация с параллельными PR'ами (rebase-protocol):

* origin/main head на момент ветвления — ``0015`` (Phase 11.0
  tree-memberships, ADR-0036). ``down_revision="0015"`` чтобы
  alembic-цепочка walking'алась корректно в этом PR в isolation
  (тесты ``alembic upgrade head`` зелёные).
* Параллельно Agent 1's PR (Phase 4.10b account-settings) использует
  ``revision="0016"``. Если Agent 1's PR landed **до** этого PR,
  ревьюер меняет ``down_revision="0015"`` → ``"0016"`` перед merge'ом —
  иначе на main появятся два head'а на 0015 и нужен alembic merge
  migration.

ID ``0017`` зарезервирован для этого PR specifically — координация
с Agent 1 (0016) и backstop'у возможному Agent 4 (если у того будет
свой 0016/0017).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать email_send_log + добавить users.email_opt_out."""
    op.add_column(
        "users",
        sa.Column(
            "email_opt_out",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "email_send_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column(
            "recipient_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "params",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_email_send_log_idempotency_key",
        ),
    )
    op.create_index(
        "ix_email_send_log_idempotency_key",
        "email_send_log",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_email_send_log_kind",
        "email_send_log",
        ["kind"],
    )
    op.create_index(
        "ix_email_send_log_recipient_user_id",
        "email_send_log",
        ["recipient_user_id"],
    )


def downgrade() -> None:
    """Drop email_send_log + users.email_opt_out."""
    op.drop_index(
        "ix_email_send_log_recipient_user_id",
        table_name="email_send_log",
    )
    op.drop_index("ix_email_send_log_kind", table_name="email_send_log")
    op.drop_index(
        "ix_email_send_log_idempotency_key",
        table_name="email_send_log",
    )
    op.drop_table("email_send_log")
    op.drop_column("users", "email_opt_out")
