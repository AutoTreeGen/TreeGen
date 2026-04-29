"""telegram_user_links.notifications_enabled — opt-in для TG-нотификаций (Phase 14.1, ADR-0056).

Revision ID: 0025
Revises: 0024
Create Date: 2026-04-30

Добавляет колонку ``notifications_enabled BOOLEAN NOT NULL DEFAULT FALSE``
в ``telegram_user_links``. Дефолт ``false`` соответствует privacy-by-default:
attached chat не получает push'ей пока user явно не вызвал ``/subscribe``.

Sequencing (post-rebase, 2026-04-30): первоначально планировалось как 0023,
но #135 (4.11b user_action_status_manual) занял 0023 и #136 (4.11c
ownership_transfer) занял 0024 на main. Эта миграция отрезалась на 0025 /
down_revision=0024 при rebase'е поверх merged main.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add notifications_enabled column with default False."""
    op.add_column(
        "telegram_user_links",
        sa.Column(
            "notifications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Drop notifications_enabled column."""
    op.drop_column("telegram_user_links", "notifications_enabled")
