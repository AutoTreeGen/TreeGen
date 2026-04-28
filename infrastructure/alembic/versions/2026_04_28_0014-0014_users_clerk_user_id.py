"""users.clerk_user_id (Phase 4.10 — Clerk auth, ADR-0033).

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-28

Добавляет nullable unique-колонку ``clerk_user_id`` для маппинга
Clerk JWT ``sub`` → ``users.id`` (UUID). Существующая колонка
``external_auth_id`` остаётся (legacy ``"local:{email}"`` значения),
``clerk_user_id`` — отдельный явный канал для Clerk-аутентификации.

JIT user-creation flow (см. ``parser_service.services.user_sync``):

1. Запрос приходит с Bearer JWT, ``shared_models.auth`` верифицирует и
   достаёт ``sub``/``email`` из claims.
2. Helper :func:`get_or_create_user_from_clerk` ищет user по
   ``clerk_user_id``; если нет — создаёт row с ``clerk_user_id=sub``,
   ``external_auth_id=f"clerk:{sub}"``, ``email`` из claims.
3. Возврат ``users.id`` для всех downstream-операций.

См. ADR-0033 §«User sync flow».
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
    """Add nullable unique ``users.clerk_user_id``."""
    op.add_column(
        "users",
        sa.Column("clerk_user_id", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_clerk_user_id",
        "users",
        ["clerk_user_id"],
    )
    # B-tree index — для прямого lookup'а по Clerk sub в JIT-flow.
    # Уникальный constraint выше уже создаёт supporting index, но явная
    # ``index=True``-семантика на ORM ожидает named index — добавляем.
    op.create_index(
        "ix_users_clerk_user_id",
        "users",
        ["clerk_user_id"],
    )


def downgrade() -> None:
    """Drop ``users.clerk_user_id`` and related index/unique."""
    op.drop_index("ix_users_clerk_user_id", table_name="users")
    op.drop_constraint("uq_users_clerk_user_id", "users", type_="unique")
    op.drop_column("users", "clerk_user_id")
