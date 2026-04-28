"""notification_preferences table (Phase 8.0 wire-up, ADR-0029).

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-28

Per-user toggles для notification event_type'ов. Composite PK
``(user_id, event_type)`` — простая семантика «одна настройка на тип
события». Если строки нет, dispatcher применяет дефолты (enabled=True,
channels=["in_app", "log"]).

`user_id` — ``BigInteger`` без FK на ``users`` (auth-слой ещё не
существует, см. notifications-миграцию 0007).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create notification_preferences table."""
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "channels",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("""'["in_app", "log"]'::jsonb"""),
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
        sa.PrimaryKeyConstraint(
            "user_id",
            "event_type",
            name="pk_notification_preferences",
        ),
    )


def downgrade() -> None:
    """Drop notification_preferences table."""
    op.drop_table("notification_preferences")
