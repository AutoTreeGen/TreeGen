"""Chat sessions + messages — AI tree-chat persistence (Phase 10.7c).

Revision ID: 0032
Revises: 0031
Create Date: 2026-05-02

Добавляет две таблицы:

* ``chat_sessions`` — один thread разговора пользователя с AI о его дереве.
* ``chat_messages`` — упорядоченные turn'ы внутри сессии (user/assistant/system).

Service-table pattern (как audio_sessions): нет provenance/version_id/
soft-delete на самих row'ах. Hard delete:

* ``chat_sessions.tree_id → trees.id ON DELETE CASCADE`` — дерево удалено,
  чаты тоже.
* ``chat_sessions.user_id → users.id ON DELETE RESTRICT`` — пользователя
  не удаляют пока есть чаты (audit-trail). GDPR-erasure обнуляет чаты до user'а.
* ``chat_sessions.anchor_person_id → persons.id ON DELETE SET NULL`` —
  если anchor-person удалён, сессия survive'ает без anchor'а (старые
  references остаются как есть).
* ``chat_messages.session_id → chat_sessions.id ON DELETE CASCADE`` —
  удаление сессии забирает messages с собой.

Индексы:

* ``ix_chat_sessions_tree_id``, ``ix_chat_sessions_user_id`` — list-views
  «мои чаты в этом дереве» / «мои чаты вообще».
* ``ix_chat_messages_session_id_created_at`` (composite) — load session
  history in order, single query per page-load.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать ``chat_sessions`` + ``chat_messages``."""
    op.create_table(
        "chat_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "anchor_person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=True),
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
    )
    op.create_index(
        "ix_chat_sessions_tree_id",
        "chat_sessions",
        ["tree_id"],
    )
    op.create_index(
        "ix_chat_sessions_user_id",
        "chat_sessions",
        ["user_id"],
    )

    op.create_table(
        "chat_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "references_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_chat_messages_role",
        ),
    )
    op.create_index(
        "ix_chat_messages_session_id_created_at",
        "chat_messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    """Удалить chat-таблицы (messages первыми из-за FK)."""
    op.drop_index("ix_chat_messages_session_id_created_at", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_tree_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
