"""Stripe billing tables (Phase 12.0, ADR-0034).

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-28

Создаёт три таблицы для Stripe-биллинга:

* ``stripe_customers`` — маппинг users.id ↔ Stripe Customer ID.
* ``stripe_subscriptions`` — текущая (или последняя) подписка пользователя
  с локальным снимком plan/status/period_end (для feature-gating без
  hot-path вызовов в Stripe).
* ``stripe_events`` — лог обработанных webhook event'ов для
  idempotency (Stripe at-least-once).

Все три таблицы — workspace-shared, поэтому миграция в общем alembic-tree.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stripe_customers, stripe_subscriptions, stripe_events."""
    op.create_table(
        "stripe_customers",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(64), nullable=False),
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
        sa.UniqueConstraint("user_id", name="uq_stripe_customers_user_id"),
        sa.UniqueConstraint(
            "stripe_customer_id",
            name="uq_stripe_customers_stripe_customer_id",
        ),
    )
    op.create_index(
        "ix_stripe_customers_user_id",
        "stripe_customers",
        ["user_id"],
    )
    op.create_index(
        "ix_stripe_customers_stripe_customer_id",
        "stripe_customers",
        ["stripe_customer_id"],
    )

    op.create_table(
        "stripe_subscriptions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stripe_sub_id", sa.String(64), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("status", sa.String(32), nullable=False, server_default="incomplete"),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
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
        sa.UniqueConstraint("user_id", name="uq_stripe_subscriptions_user_id"),
        sa.UniqueConstraint(
            "stripe_sub_id",
            name="uq_stripe_subscriptions_stripe_sub_id",
        ),
    )
    op.create_index(
        "ix_stripe_subscriptions_user_id",
        "stripe_subscriptions",
        ["user_id"],
    )
    op.create_index(
        "ix_stripe_subscriptions_stripe_sub_id",
        "stripe_subscriptions",
        ["stripe_sub_id"],
    )

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("stripe_event_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            "stripe_event_id",
            name="uq_stripe_events_stripe_event_id",
        ),
    )
    op.create_index(
        "ix_stripe_events_stripe_event_id",
        "stripe_events",
        ["stripe_event_id"],
    )
    op.create_index(
        "ix_stripe_events_event_type",
        "stripe_events",
        ["event_type"],
    )


def downgrade() -> None:
    """Drop stripe_events, stripe_subscriptions, stripe_customers."""
    op.drop_index("ix_stripe_events_event_type", table_name="stripe_events")
    op.drop_index("ix_stripe_events_stripe_event_id", table_name="stripe_events")
    op.drop_table("stripe_events")

    op.drop_index(
        "ix_stripe_subscriptions_stripe_sub_id",
        table_name="stripe_subscriptions",
    )
    op.drop_index(
        "ix_stripe_subscriptions_user_id",
        table_name="stripe_subscriptions",
    )
    op.drop_table("stripe_subscriptions")

    op.drop_index(
        "ix_stripe_customers_stripe_customer_id",
        table_name="stripe_customers",
    )
    op.drop_index("ix_stripe_customers_user_id", table_name="stripe_customers")
    op.drop_table("stripe_customers")
