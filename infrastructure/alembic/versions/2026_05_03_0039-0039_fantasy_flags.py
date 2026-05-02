"""fantasy_flags + dismiss-lifecycle (Phase 5.10 / ADR-0077).

Revision ID: 0039
Revises: 0038
Create Date: 2026-05-03

Создаёт таблицу ``fantasy_flags`` под advisory findings от rule-based
fabrication detector (Phase 5.10 GEDCOM Doctor stack final). См. ORM
``shared_models.orm.fantasy_flag.FantasyFlag``.

Service-table: ``tree_id`` FK CASCADE, ``dismissed_by`` FK SET NULL на
users; нет provenance/version_id/soft-delete (audit-row, не domain-fact).

Indexes:

* ``ix_fantasy_flags_tree_id`` — leftmost для FK / per-tree фильтра.
* ``ix_fantasy_flags_tree_severity_dismissed`` — главный list-query UI.
* ``ix_fantasy_flags_subject_person`` — person-detail-страница.
* ``ix_fantasy_flags_rule_id`` — analytics: «сколько раз сработало
  каждое правило», аудит-trend over time.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``fantasy_flags``."""
    op.create_table(
        "fantasy_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "subject_relationship_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "dismissed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
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
            "subject_person_id IS NOT NULL OR subject_relationship_id IS NOT NULL",
            name="ck_fantasy_flags_has_subject",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'high', 'critical')",
            name="ck_fantasy_flags_severity",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_fantasy_flags_confidence_range",
        ),
        sa.CheckConstraint(
            "(dismissed_at IS NULL AND dismissed_by IS NULL AND dismissed_reason IS NULL)"
            " OR (dismissed_at IS NOT NULL)",
            name="ck_fantasy_flags_dismiss_consistency",
        ),
    )
    op.create_index("ix_fantasy_flags_tree_id", "fantasy_flags", ["tree_id"])
    op.create_index(
        "ix_fantasy_flags_tree_severity_dismissed",
        "fantasy_flags",
        ["tree_id", "severity", "dismissed_at"],
    )
    op.create_index(
        "ix_fantasy_flags_subject_person",
        "fantasy_flags",
        ["subject_person_id", "dismissed_at"],
    )
    op.create_index("ix_fantasy_flags_rule_id", "fantasy_flags", ["rule_id"])


def downgrade() -> None:
    """Drop fantasy_flags."""
    op.drop_index("ix_fantasy_flags_rule_id", table_name="fantasy_flags")
    op.drop_index("ix_fantasy_flags_subject_person", table_name="fantasy_flags")
    op.drop_index("ix_fantasy_flags_tree_severity_dismissed", table_name="fantasy_flags")
    op.drop_index("ix_fantasy_flags_tree_id", table_name="fantasy_flags")
    op.drop_table("fantasy_flags")
