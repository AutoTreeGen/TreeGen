"""Phase 8.0 — notifications table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-27

См. ADR-0024. Создаёт таблицу `notifications` с partial unique index
по ``(user_id, event_type, idempotency_key)`` для свежих (за последний
час) записей — это материализация idempotency-окна.

Замечание про `user_id`: пока auth-слоя нет, `user_id` — просто
``BigInteger`` без FK на отсутствующую таблицу `users`. Когда
Phase 4.x добавит auth, FK добавится отдельной миграцией.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create notifications table + indices."""
    op.create_table(
        "notifications",
        # IdMixin / TimestampMixin.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        # Phase 8.0 specific.
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "channels_attempted",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Unread-counter в шапке UI: индекс по (user_id, read_at) с
    # частичным предикатом read_at IS NULL — быстрый count.
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    # Полный список нотификаций пользователя (sorted by created_at desc).
    op.create_index(
        "ix_notifications_user_created_at",
        "notifications",
        ["user_id", "created_at"],
    )
    # Idempotency lookup: быстрый поиск свежих нотификаций по
    # ``(user_id, event_type, idempotency_key)``. NOT UNIQUE —
    # Postgres не разрешает predicate с NOW() в unique partial index
    # (требование IMMUTABLE-функций). Поэтому idempotency проверяется
    # в dispatcher через check-and-insert внутри транзакции
    # (см. ``services/notification-service/services/dispatcher.py``).
    # Race-condition между check и insert маловероятен на нашем
    # ожидаемом RPS и в худшем случае даёт второй INSERT — это
    # at-least-once и допустимо по ADR-0024.
    op.create_index(
        "ix_notifications_idempotency_lookup",
        "notifications",
        ["user_id", "event_type", "idempotency_key", "created_at"],
    )
    # Аналитика по типу событий (administrative reports).
    op.create_index(
        "ix_notifications_event_type_created",
        "notifications",
        ["event_type", "created_at"],
    )


def downgrade() -> None:
    """Drop notifications table."""
    op.drop_index("ix_notifications_event_type_created", table_name="notifications")
    op.drop_index("ix_notifications_idempotency_lookup", table_name="notifications")
    op.drop_index("ix_notifications_user_created_at", table_name="notifications")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_table("notifications")
