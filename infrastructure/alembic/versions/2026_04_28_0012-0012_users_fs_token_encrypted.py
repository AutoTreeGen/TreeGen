"""users.fs_token_encrypted (Phase 5.1, ADR-0027).

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-28

Добавляет nullable text-колонку для хранения Fernet-зашифрованного
JSON-payload'а с FamilySearch OAuth-токенами:

* access_token, refresh_token, expires_at, scope, fs_user_id, stored_at.

NULL = пользователь не подключал FamilySearch (или disconnected).
Шифрование — application-level (cryptography.Fernet) с ключом из ENV
``PARSER_SERVICE_FS_TOKEN_KEY``. См. ADR-0027 §«Решение» для формата
payload'а и ротации ключа.

Колонка добавляется только для server-side OAuth flow Phase 5.1+;
синхронный stateless-импорт (POST /imports/familysearch с access_token
в body) продолжает работать без сохранения токена.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable text column users.fs_token_encrypted."""
    op.add_column(
        "users",
        sa.Column("fs_token_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the FS-token column."""
    op.drop_column("users", "fs_token_encrypted")
