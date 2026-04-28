"""tree_memberships + tree_invitations (Phase 11.0).

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-28

См. ADR-0036 «Sharing & permissions model».

Создаёт две таблицы для row-level sharing-flow и backfill'ит
``tree_memberships`` записями OWNER для всех уже существующих trees
(каждый Tree.owner_user_id → membership с role='owner').

Legacy ``tree_collaborators`` (создана 0001_initial_schema, но никогда
не использовалась в API) — не трогаем; дроп отдельной миграцией после
Phase 11.1, когда новый flow прокатан.

Partial unique index ``uq_tree_memberships_one_owner_per_tree``
гарантирует ровно один OWNER на дерево на DB-уровне без app-side
race-condition'ов: ``ON tree_memberships (tree_id) WHERE role='owner'
AND revoked_at IS NULL``. Ноль OWNER'ов — допустим (например, после
revoke owner'а UI принудит сделать transfer; в этом окне дерево
без active OWNER, но row в audit-логе остаётся).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tree_memberships + tree_invitations + backfill existing OWNER rows."""
    # ---- tree_memberships ---------------------------------------------------
    op.create_table(
        "tree_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="viewer"),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_tree_memberships_tree_id_trees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_tree_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_tree_memberships_invited_by_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("tree_id", "user_id", name="uq_tree_memberships_tree_id_user_id"),
    )
    op.create_index("ix_tree_memberships_tree", "tree_memberships", ["tree_id"])
    op.create_index("ix_tree_memberships_user", "tree_memberships", ["user_id"])

    # Partial unique: один OWNER на дерево среди active-rows.
    # raw SQL — alembic's create_index не умеет partial expression elegantly
    # на старых версиях.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_tree_memberships_one_owner_per_tree
        ON tree_memberships (tree_id)
        WHERE role = 'owner' AND revoked_at IS NULL
        """,
    )

    # ---- tree_invitations ---------------------------------------------------
    op.create_table(
        "tree_invitations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inviter_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invitee_email", sa.String(length=254), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="viewer"),
        sa.Column(
            "token",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_tree_invitations_tree_id_trees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_user_id"],
            ["users.id"],
            name="fk_tree_invitations_inviter_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name="fk_tree_invitations_accepted_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name="fk_tree_invitations_revoked_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_tree_invitations_tree", "tree_invitations", ["tree_id"])
    op.create_index(
        "ix_tree_invitations_token",
        "tree_invitations",
        ["token"],
        unique=True,
    )
    op.create_index("ix_tree_invitations_invitee_email", "tree_invitations", ["invitee_email"])

    # ---- backfill OWNER membership for existing trees -----------------------
    # Каждое уже существующее дерево получает OWNER-membership = trees.owner_user_id.
    # Без этого permission gate'ы сразу после деплоя отдадут 403 на
    # собственное дерево владельца. invited_by = NULL — система-инициированный
    # backfill, не human invite.
    op.execute(
        """
        INSERT INTO tree_memberships (id, tree_id, user_id, role, invited_by, accepted_at, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            t.id,
            t.owner_user_id,
            'owner',
            NULL,
            now(),
            now(),
            now()
        FROM trees t
        WHERE NOT EXISTS (
            SELECT 1 FROM tree_memberships m
            WHERE m.tree_id = t.id AND m.user_id = t.owner_user_id AND m.role = 'owner'
        )
        """,
    )


def downgrade() -> None:
    """Drop both tables. Backfilled data is not preserved."""
    op.drop_index("ix_tree_invitations_invitee_email", table_name="tree_invitations")
    op.drop_index("ix_tree_invitations_token", table_name="tree_invitations")
    op.drop_index("ix_tree_invitations_tree", table_name="tree_invitations")
    op.drop_table("tree_invitations")

    op.execute("DROP INDEX IF EXISTS uq_tree_memberships_one_owner_per_tree")
    op.drop_index("ix_tree_memberships_user", table_name="tree_memberships")
    op.drop_index("ix_tree_memberships_tree", table_name="tree_memberships")
    op.drop_table("tree_memberships")
