"""Telegram user links — opt-in linking of TG-chat to TreeGen user (Phase 14.0, ADR-0040).

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-29

Создаёт ``telegram_user_links`` — service-table связи TreeGen-user'а
с Telegram chat_id. Заполняется только через явный opt-in flow
(см. ADR-0040 §«Account linking flow»). Без soft-delete; revocation
- ``revoked_at`` timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать telegram_user_links."""
    op.create_table(
        "telegram_user_links",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
            "tg_chat_id",
            name="uq_telegram_user_links_tg_chat_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "tg_chat_id",
            name="uq_telegram_user_links_user_chat",
        ),
    )
    op.create_index(
        "ix_telegram_user_links_user_id",
        "telegram_user_links",
        ["user_id"],
    )
    op.create_index(
        "ix_telegram_user_links_tg_chat_id",
        "telegram_user_links",
        ["tg_chat_id"],
    )


def downgrade() -> None:
    """Drop telegram_user_links."""
    op.drop_index(
        "ix_telegram_user_links_tg_chat_id",
        table_name="telegram_user_links",
    )
    op.drop_index(
        "ix_telegram_user_links_user_id",
        table_name="telegram_user_links",
    )
    op.drop_table("telegram_user_links")
