"""public_tree_shares table (Phase 11.2 — ADR-0047).

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-29

Public read-only share-link для дерева. Owner создаёт через
``POST /trees/{id}/public-share``; полученный ``share_token`` (URL-safe
random ~20 chars) используется в ``GET /public/trees/{token}`` без
аутентификации (DNA-данные вырезаны, alive persons анонимизированы —
см. ADR-0047 §«Privacy»).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать таблицу + индексы (UNIQUE на token, btree на tree_id)."""
    op.create_table(
        "public_tree_shares",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "share_token",
            sa.String(32),
            nullable=False,
            comment=("URL-safe random token (~20 chars). App-side: secrets.token_urlsafe(15)."),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="NULL = never expires; revocation через revoked_at.",
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_public_tree_shares_token",
        "public_tree_shares",
        ["share_token"],
        unique=True,
    )
    op.create_index(
        "ix_public_tree_shares_tree",
        "public_tree_shares",
        ["tree_id"],
    )


def downgrade() -> None:
    """Снести индексы и таблицу. Все public-share rows теряются."""
    op.drop_index("ix_public_tree_shares_tree", table_name="public_tree_shares")
    op.drop_index("ix_public_tree_shares_token", table_name="public_tree_shares")
    op.drop_table("public_tree_shares")
