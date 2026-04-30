"""tree_change_proposals + protected tree mode (Phase 16.1a / ADR-0062).

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-01

Adds the data-model layer for "Genealogy Git" — PR-style change review
для деревьев + Protected Tree Mode toggle.

Что создаётся:

* ``trees.protected`` (boolean, default false) — opt-in флаг. Пока
  ``false`` — direct edits разрешены (текущее поведение). Когда ``true``
  — все мутации должны идти через ``tree_change_proposals``.
* ``trees.protection_policy`` (jsonb, default ``{}``) — конфигурация
  protection: ``require_evidence_for`` (list of relationship kinds),
  ``min_reviewers`` (int), ``allow_owner_bypass`` (bool). Чтение —
  через Pydantic-схему в ``api_gateway.schemas`` (валидация формы).
* ``tree_change_proposals`` — заголовок + diff_jsonb + state machine
  ``open → approved/rejected → merged → rolled_back``. ``author_user_id``
  и ``reviewed_by_user_id`` — UUID FK на ``users.id`` (CASCADE on delete
  для GDPR-erasure; см. ADR-0062 §«FK strategy»).
* ``tree_change_proposal_evidence`` — many-to-many с ``sources``,
  attached на конкретный proposal с opaque ``relationship_ref`` jsonb
  (caller указывает, какой change support'ит этот источник).

Не TreeEntity — это audit/workflow log (без provenance/version_id/
status/soft-delete). См. ``test_schema_invariants.SERVICE_TABLES``.

Phase 16.1b/c добавит endpoint'ы review/approve/merge; здесь только
schema + минимальный CRUD (16.1a).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add protected/protection_policy to trees + create proposal tables."""
    op.add_column(
        "trees",
        sa.Column(
            "protected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "trees",
        sa.Column(
            "protection_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "tree_change_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "diff",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="open",
        ),
        sa.Column(
            "evidence_required",
            postgresql.JSONB(),
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
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "merge_commit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_log.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rolled_back_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('open','approved','rejected','merged','rolled_back')",
            name="ck_tree_change_proposals_status",
        ),
    )
    op.create_index(
        "ix_tree_change_proposals_tree_status",
        "tree_change_proposals",
        ["tree_id", "status"],
    )
    op.create_index(
        "ix_tree_change_proposals_author",
        "tree_change_proposals",
        ["author_user_id"],
    )

    op.create_table(
        "tree_change_proposal_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tree_change_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("citation", sa.Text(), nullable=False),
        sa.Column(
            "relationship_ref",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "added_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Один источник может быть добавлен несколько раз с разным
    # relationship_ref (поддерживаем same source как доказательство для
    # разных предлагаемых изменений), поэтому UNIQUE по тройке.
    op.create_index(
        "ux_tree_change_proposal_evidence_unique",
        "tree_change_proposal_evidence",
        ["proposal_id", "source_id", "relationship_ref"],
        unique=True,
    )
    op.create_index(
        "ix_tree_change_proposal_evidence_proposal",
        "tree_change_proposal_evidence",
        ["proposal_id"],
    )


def downgrade() -> None:
    """Drop proposal tables + remove trees.protected/policy columns."""
    op.drop_index(
        "ix_tree_change_proposal_evidence_proposal",
        table_name="tree_change_proposal_evidence",
    )
    op.drop_index(
        "ux_tree_change_proposal_evidence_unique",
        table_name="tree_change_proposal_evidence",
    )
    op.drop_table("tree_change_proposal_evidence")

    op.drop_index(
        "ix_tree_change_proposals_author",
        table_name="tree_change_proposals",
    )
    op.drop_index(
        "ix_tree_change_proposals_tree_status",
        table_name="tree_change_proposals",
    )
    op.drop_table("tree_change_proposals")

    op.drop_column("trees", "protection_policy")
    op.drop_column("trees", "protected")
