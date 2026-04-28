"""User account settings + action requests (Phase 4.10b, ADR-0038).

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-29

Добавляет:

* ``users.timezone`` — IANA timezone string (например, ``"Europe/Moscow"``),
  nullable. Используется бекендом для рендера дат в письмах/notification'ах
  и фронтендом для local-time-aware виджетов. ``display_name`` и ``locale``
  уже были в schema (Phase 2), здесь не трогаем.
* ``user_action_requests`` — таблица request'ов на пользовательские
  действия с side-effect-ами (export своих данных, erasure аккаунта).
  Phase 4.10b создаёт row'ы как stub (status=``pending``); Phase 4.11
  (Agent 5) добавит processing-pipeline (worker, file generation,
  signed download URL'ы для export, hard-delete cascade для erasure).

Дизайн ``user_action_requests`` — общая таблица для разных kind'ов
(не отдельные ``export_requests`` / ``erasure_requests``), потому что
жизненный цикл одинаковый (pending → processing → done/failed),
schema row'ы 95% общая, и Phase 4.11 будет шарить worker-handler.

См. ADR-0038 §«Stub-now / process-in-4.11 split».
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``users.timezone`` + create ``user_action_requests``."""
    op.add_column(
        "users",
        sa.Column("timezone", sa.Text(), nullable=True),
    )

    op.create_table(
        "user_action_requests",
        # IdMixin + TimestampMixin (audit-style).
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
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "request_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_action_requests_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('export', 'erasure')",
            name="ck_user_action_requests_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed', 'cancelled')",
            name="ck_user_action_requests_status",
        ),
    )
    # Lookup «pending requests этого user'а» — основной запрос UI.
    op.create_index(
        "ix_user_action_requests_user_id",
        "user_action_requests",
        ["user_id"],
    )
    # Worker-side scan «найти все pending», единственный вход для Phase 4.11
    # processing-loop. Без партиал-индекса — pending-набор маленький, full
    # scan быстрее чем поддержка нескольких индексов.
    op.create_index(
        "ix_user_action_requests_status",
        "user_action_requests",
        ["status"],
    )


def downgrade() -> None:
    """Drop ``user_action_requests`` + ``users.timezone``."""
    op.drop_index("ix_user_action_requests_status", table_name="user_action_requests")
    op.drop_index("ix_user_action_requests_user_id", table_name="user_action_requests")
    op.drop_table("user_action_requests")
    op.drop_column("users", "timezone")
