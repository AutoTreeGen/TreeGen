"""PersonMergeLog table (Phase 4.6 — ADR-0022).

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-27

Добавляет таблицу `person_merge_logs` для audit-trail manual-merge'ей
двух персон с полным `dry_run_diff_json`-snapshot'ом и 90-дневным undo-окном.

CLAUDE.md §5 invariant теперь enforce'ится в коде: backend требует
explicit `confirm:true` в payload commit'а; этот лог — единственный
источник truth для отката (`undone_at`) и retention (`purged_at`).

Партиал-индекс `uq_person_merge_logs_active` гарантирует идемпотентность
повторного POST'а с тем же `confirm_token` для активного merge'а одной
пары: вторая попытка не создаёт новой строки, а попадает в `IntegrityError`,
который backend ловит и возвращает существующий лог row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create person_merge_logs table + indexes."""
    op.create_table(
        "person_merge_logs",
        # IdMixin + TimestampMixin (без TreeEntityMixins — это лог,
        # не доменная запись дерева).
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
        # Phase 4.6 specific.
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("survivor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merged_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "merged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("merged_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirm_token", sa.String(64), nullable=False),
        sa.Column(
            "dry_run_diff_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_person_merge_logs_tree_id_trees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["survivor_id"],
            ["persons.id"],
            name="fk_person_merge_logs_survivor_id_persons",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["merged_id"],
            ["persons.id"],
            name="fk_person_merge_logs_merged_id_persons",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["merged_by_user_id"],
            ["users.id"],
            name="fk_person_merge_logs_merged_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["undone_by_user_id"],
            ["users.id"],
            name="fk_person_merge_logs_undone_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    # Простой индекс на tree_id (для tree-scoped lookups).
    op.create_index(
        "ix_person_merge_logs_tree_id",
        "person_merge_logs",
        ["tree_id"],
    )
    # История merge'ей конкретной персоны: «View merge history» в UI.
    op.create_index(
        "ix_person_merge_logs_survivor",
        "person_merge_logs",
        ["tree_id", "survivor_id"],
    )
    op.create_index(
        "ix_person_merge_logs_merged",
        "person_merge_logs",
        ["tree_id", "merged_id"],
    )
    # Retention sweep: «найти все merge'и старше 90 дней без purged_at».
    op.create_index(
        "ix_person_merge_logs_merged_at",
        "person_merge_logs",
        ["merged_at"],
    )
    # Идемпотентность активного merge'а: уникальность по
    # (tree_id, survivor_id, merged_id, confirm_token) среди НЕ-undone и
    # НЕ-purged строк. Партиал-индекс через postgresql_where.
    op.create_index(
        "uq_person_merge_logs_active",
        "person_merge_logs",
        ["tree_id", "survivor_id", "merged_id", "confirm_token"],
        unique=True,
        postgresql_where=sa.text("undone_at IS NULL AND purged_at IS NULL"),
    )


def downgrade() -> None:
    """Drop person_merge_logs."""
    op.drop_index("uq_person_merge_logs_active", table_name="person_merge_logs")
    op.drop_index("ix_person_merge_logs_merged_at", table_name="person_merge_logs")
    op.drop_index("ix_person_merge_logs_merged", table_name="person_merge_logs")
    op.drop_index("ix_person_merge_logs_survivor", table_name="person_merge_logs")
    op.drop_index("ix_person_merge_logs_tree_id", table_name="person_merge_logs")
    op.drop_table("person_merge_logs")
