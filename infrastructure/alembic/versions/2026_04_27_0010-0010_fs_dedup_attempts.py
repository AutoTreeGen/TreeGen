"""FsDedupAttempt table (Phase 5.2.1).

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-27

Узкая таблица ``fs_dedup_attempts`` для FS-flagged пар (см.
``docs/research/phase-5-2-dedup-discovery.md`` Option C). Хранит
кандидатов, обнаруженных скорером после ``import_fs_pedigree``: пара
*(только что импортированная FS-персона, локальная не-FS персона того же
дерева)* со ``score >= 0.6``. Никакого автомата merge — только
suggestion для review-UI (CLAUDE.md §5).

Партиал-уникальный индекс ``ux_fs_dedup_attempts_active_pair`` гарантирует:
для каждой направленной пары (fs_person_id, candidate_person_id) внутри
дерева может существовать максимум одна active-attempt запись
(``rejected_at IS NULL AND merged_at IS NULL``). Reject'нутые и merged
строки уже не active и не блокируют новый attempt — это нужно для
90-day cooldown (importer отдельно фильтрует свежие reject'ы).

Direction matters: пара не lex-reorder'ится. ``(A=fs, B=local)`` и
``(B=fs, A=local)`` — разные attempts, и индекс это позволяет (они
просто разные тройки).

CHECK constraints:

* ``ck_fs_dedup_attempts_score_range`` — score в [0, 1].
* ``ck_fs_dedup_attempts_distinct_persons`` — fs_person_id != candidate_person_id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create fs_dedup_attempts table + indexes + constraints."""
    op.create_table(
        "fs_dedup_attempts",
        # IdMixin + TimestampMixin (audit-style row, без TreeEntityMixins —
        # это лог attempt'ов, не доменная запись дерева).
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
        # Phase 5.2.1 specific.
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fs_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("fs_pid", sa.String(64), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_fs_dedup_attempts_tree_id_trees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fs_person_id"],
            ["persons.id"],
            name="fk_fs_dedup_attempts_fs_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_person_id"],
            ["persons.id"],
            name="fk_fs_dedup_attempts_candidate_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_fs_dedup_attempts_score_range",
        ),
        sa.CheckConstraint(
            "fs_person_id <> candidate_person_id",
            name="ck_fs_dedup_attempts_distinct_persons",
        ),
    )
    # FK lookup indexes (mirror ORM `index=True` на колонках).
    op.create_index(
        "ix_fs_dedup_attempts_tree_id",
        "fs_dedup_attempts",
        ["tree_id"],
    )
    op.create_index(
        "ix_fs_dedup_attempts_fs_person_id",
        "fs_dedup_attempts",
        ["fs_person_id"],
    )
    op.create_index(
        "ix_fs_dedup_attempts_candidate_person_id",
        "fs_dedup_attempts",
        ["candidate_person_id"],
    )
    # Idempotency lookup: «уже было merged для этого fs_pid?».
    op.create_index(
        "ix_fs_dedup_attempts_tree_id_fs_pid",
        "fs_dedup_attempts",
        ["tree_id", "fs_pid"],
    )
    # Active-pair partial unique: один active attempt на направленную пару.
    op.create_index(
        "ux_fs_dedup_attempts_active_pair",
        "fs_dedup_attempts",
        ["tree_id", "fs_person_id", "candidate_person_id"],
        unique=True,
        postgresql_where=sa.text("rejected_at IS NULL AND merged_at IS NULL"),
    )


def downgrade() -> None:
    """Drop fs_dedup_attempts table + indexes."""
    op.drop_index("ux_fs_dedup_attempts_active_pair", table_name="fs_dedup_attempts")
    op.drop_index("ix_fs_dedup_attempts_tree_id_fs_pid", table_name="fs_dedup_attempts")
    op.drop_index("ix_fs_dedup_attempts_candidate_person_id", table_name="fs_dedup_attempts")
    op.drop_index("ix_fs_dedup_attempts_fs_person_id", table_name="fs_dedup_attempts")
    op.drop_index("ix_fs_dedup_attempts_tree_id", table_name="fs_dedup_attempts")
    op.drop_table("fs_dedup_attempts")
