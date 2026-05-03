"""Completeness assertions / sealed sets (Phase 15.11a).

Revision ID: 0040
Revises: 0034
Create Date: 2026-05-02

Добавляет две таблицы для owner-asserted-negation flag'ов на scope вокруг
анкорной персоны («siblings of X are exhaustive»):

* ``completeness_assertions`` — TreeEntity с soft-delete / provenance /
  version_id; partial-unique по ``(tree_id, subject_person_id, scope)``
  для активных rows (``deleted_at IS NULL``).
* ``completeness_assertion_sources`` — junction (assertion ↔ source),
  composite PK, CASCADE on assertion delete, RESTRICT on source delete
  (sources outlive citations).

Source-count invariant (≥1) НЕ ENFORCED на уровне БД — Postgres не
выражает «≥1 row в child table» без триггеров. Service-layer проверка
с TODO для 15.11b — см. ADR-0076.

См. также brief ``docs/briefs/phase-15-11a-completeness-assertions.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать ``completeness_assertions`` + ``completeness_assertion_sources``."""
    op.create_table(
        "completeness_assertions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "subject_person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column(
            "is_sealed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "asserted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "asserted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        # TreeEntityMixins: status / confidence / provenance / version_id /
        # timestamps / soft-delete.
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="probable",
        ),
        sa.Column(
            "confidence_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.5"),
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "version_id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_completeness_assertions_tree_id",
        "completeness_assertions",
        ["tree_id"],
    )
    op.create_index(
        "ix_completeness_assertions_subject_person_id",
        "completeness_assertions",
        ["subject_person_id"],
    )
    op.create_index(
        "ix_completeness_assertions_asserted_by",
        "completeness_assertions",
        ["asserted_by"],
    )
    # Partial-unique по активным rows: одна assertion-row на (tree, person,
    # scope) среди не-удалённых. Soft-delete'ом можно «выкатить» старую и
    # создать новую, не нарушая uniqueness.
    op.create_index(
        "uq_completeness_assertion_tree_person_scope_active",
        "completeness_assertions",
        ["tree_id", "subject_person_id", "scope"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "completeness_assertion_sources",
        sa.Column(
            "assertion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("completeness_assertions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    """Удалить таблицы в обратном порядке (junction → parent)."""
    op.drop_table("completeness_assertion_sources")
    op.drop_index(
        "uq_completeness_assertion_tree_person_scope_active",
        table_name="completeness_assertions",
    )
    op.drop_index(
        "ix_completeness_assertions_asserted_by",
        table_name="completeness_assertions",
    )
    op.drop_index(
        "ix_completeness_assertions_subject_person_id",
        table_name="completeness_assertions",
    )
    op.drop_index(
        "ix_completeness_assertions_tree_id",
        table_name="completeness_assertions",
    )
    op.drop_table("completeness_assertions")
