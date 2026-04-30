"""Stripe billing tables — customers, subscriptions, event log (Phase 12.0, ADR-0042).

Revision ID: 0027
Revises: 0026
Create Date: 2026-04-30

Создаёт три service-table'а под Stripe-биллинг:

* ``stripe_customers`` — маппинг users.id ↔ Stripe Customer ID
  (one-to-one).
* ``subscriptions`` — canonical billing state per user. Мутируется
  ТОЛЬКО webhook'ами (никогда не application-side).
* ``stripe_event_log`` — webhook idempotency + audit trail
  (``stripe_event_id UNIQUE`` для idempotent dispatch).

Plan / status сохраняются как ``text`` с CHECK-constraint'ом, а не
PostgreSQL ENUM type — дешевле миграции, проще миксовать новые
значения. Допустимые значения мапятся 1-в-1 на StrEnum'ы из
``shared_models.enums``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PLAN_VALUES = ("free", "pro", "premium")
_SUB_STATUS_VALUES = ("active", "past_due", "canceled", "trialing")
_EVENT_STATUS_VALUES = ("received", "processed", "failed")


def _values_clause(name: str, values: tuple[str, ...]) -> str:
    """Сформировать ``col IN ('a','b',...)`` для CHECK-constraint."""
    quoted = ",".join(f"'{v}'" for v in values)
    return f"{name} IN ({quoted})"


def upgrade() -> None:
    """Создать stripe_customers, subscriptions, stripe_event_log."""
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
        "subscriptions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stripe_subscription_id", sa.String(64), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
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
        sa.UniqueConstraint(
            "stripe_subscription_id",
            name="uq_subscriptions_stripe_subscription_id",
        ),
        sa.CheckConstraint(
            _values_clause("plan", _PLAN_VALUES),
            name="ck_subscriptions_plan",
        ),
        sa.CheckConstraint(
            _values_clause("status", _SUB_STATUS_VALUES),
            name="ck_subscriptions_status",
        ),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index(
        "ix_subscriptions_stripe_subscription_id",
        "subscriptions",
        ["stripe_subscription_id"],
    )

    op.create_table(
        "stripe_event_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("stripe_event_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
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
            name="uq_stripe_event_log_stripe_event_id",
        ),
        sa.CheckConstraint(
            _values_clause("status", _EVENT_STATUS_VALUES),
            name="ck_stripe_event_log_status",
        ),
    )
    op.create_index(
        "ix_stripe_event_log_stripe_event_id",
        "stripe_event_log",
        ["stripe_event_id"],
    )
    op.create_index(
        "ix_stripe_event_log_kind",
        "stripe_event_log",
        ["kind"],
    )


def downgrade() -> None:
    """Drop stripe_event_log, subscriptions, stripe_customers."""
    op.drop_index("ix_stripe_event_log_kind", table_name="stripe_event_log")
    op.drop_index(
        "ix_stripe_event_log_stripe_event_id",
        table_name="stripe_event_log",
    )
    op.drop_table("stripe_event_log")

    op.drop_index(
        "ix_subscriptions_stripe_subscription_id",
        table_name="subscriptions",
    )
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index(
        "ix_stripe_customers_stripe_customer_id",
        table_name="stripe_customers",
    )
    op.drop_index("ix_stripe_customers_user_id", table_name="stripe_customers")
    op.drop_table("stripe_customers")
