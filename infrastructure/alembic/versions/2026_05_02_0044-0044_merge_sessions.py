"""Merge sessions / decisions / apply batches — Phase 5.7c-a (ADR-0070).

Revision ID: 0035
Revises: 0034
Create Date: 2026-05-02

Добавляет три таблицы для сессионного 2-way merge'а:

* ``merge_sessions`` — long-running session row с polymorphic
  ``*_ref_kind`` / ``*_ref_id``.
* ``merge_decisions`` — per-field/per-entity решения внутри сессии.
* ``merge_apply_batches`` — зафиксированные batch'и применения decisions.

Service-table pattern (как ``audio_sessions`` / ``chat_sessions``):
нет provenance/version_id/soft-delete на самих row'ах. Provenance
пишется на затронутые domain-row'ы при apply (ADR-0070 §«Аудит и
provenance»).

Polymorphic refs: ``left_ref_id`` / ``right_ref_id`` — UUID **без**
FK-constraint'а. ``*_ref_kind`` ∈ {``imported_doc``, ``tree``,
``snapshot``}; CHECK на enum'е и **отдельный CHECK на запрет
``snapshot``** для Phase 7 (снимется в Phase 11+ отдельной миграцией,
ADR-0070 §«Когда пересмотреть»).

ALEMBIC GATE: revision=0035 — слот зарезервирован за Phase 5.7c-a после
координации с Agent 4 (Phase 5.8 GEDCOM validator занимает 0034).
``down_revision = "0034"`` — миграция чейнится поверх 0034; CI / local
alembic могут показать ``KeyError: '0034'`` пока PR Phase 5.8 не залит
в main.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать ``merge_sessions``, ``merge_decisions``, ``merge_apply_batches``."""
    op.create_table(
        "merge_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("left_ref_kind", sa.String(16), nullable=False),
        sa.Column(
            "left_ref_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("right_ref_kind", sa.String(16), nullable=False),
        sa.Column(
            "right_ref_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
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
            "status IN ('pending', 'in_progress', 'ready_to_apply', "
            "'partially_applied', 'applied', 'abandoned')",
            name="ck_merge_sessions_status",
        ),
        sa.CheckConstraint(
            "left_ref_kind IN ('imported_doc', 'tree', 'snapshot')",
            name="ck_merge_sessions_left_ref_kind",
        ),
        sa.CheckConstraint(
            "right_ref_kind IN ('imported_doc', 'tree', 'snapshot')",
            name="ck_merge_sessions_right_ref_kind",
        ),
        # Phase 7 запрет: snapshot reserved, не используется. Снимется
        # отдельной миграцией в Phase 11+ (ADR-0070).
        sa.CheckConstraint(
            "left_ref_kind <> 'snapshot' AND right_ref_kind <> 'snapshot'",
            name="ck_merge_sessions_no_snapshot_phase7",
        ),
    )
    op.create_index(
        "ix_merge_sessions_user_status_last_active",
        "merge_sessions",
        ["user_id", "status", "last_active_at"],
    )
    op.create_index(
        "ix_merge_sessions_left_ref",
        "merge_sessions",
        ["left_ref_kind", "left_ref_id"],
    )
    op.create_index(
        "ix_merge_sessions_right_ref",
        "merge_sessions",
        ["right_ref_kind", "right_ref_id"],
    )

    op.create_table(
        "merge_apply_batches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merge_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "applied_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "apply_log_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
    )
    op.create_index(
        "ix_merge_apply_batches_session_id_applied_at",
        "merge_apply_batches",
        ["session_id", "applied_at"],
    )

    op.create_table(
        "merge_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merge_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "field_path",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("chosen_source", sa.String(8), nullable=False),
        sa.Column(
            "custom_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("decision_method", sa.String(8), nullable=False),
        sa.Column("rule_id", sa.String(128), nullable=True),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "applied_in_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merge_apply_batches.id", ondelete="SET NULL"),
            nullable=True,
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
            "scope IN ('person', 'relation', 'source', 'media')",
            name="ck_merge_decisions_scope",
        ),
        sa.CheckConstraint(
            "chosen_source IN ('left', 'right', 'both', 'custom', 'skip')",
            name="ck_merge_decisions_chosen_source",
        ),
        sa.CheckConstraint(
            "decision_method IN ('manual', 'auto', 'rule')",
            name="ck_merge_decisions_decision_method",
        ),
        sa.CheckConstraint(
            "(decision_method = 'rule' AND rule_id IS NOT NULL) "
            "OR (decision_method <> 'rule' AND rule_id IS NULL)",
            name="ck_merge_decisions_rule_id_consistency",
        ),
        sa.CheckConstraint(
            "(chosen_source = 'custom' AND custom_value IS NOT NULL) "
            "OR (chosen_source <> 'custom' AND custom_value IS NULL)",
            name="ck_merge_decisions_custom_value_consistency",
        ),
    )
    op.create_index(
        "ix_merge_decisions_session_id_decided_at",
        "merge_decisions",
        ["session_id", "decided_at"],
    )
    op.create_index(
        "ix_merge_decisions_session_target",
        "merge_decisions",
        ["session_id", "target_kind", "target_id"],
    )
    op.create_index(
        "ix_merge_decisions_applied_in_batch",
        "merge_decisions",
        ["applied_in_batch_id"],
    )


def downgrade() -> None:
    """Удалить merge_*-таблицы (decisions первыми из-за FK на batches)."""
    op.drop_index("ix_merge_decisions_applied_in_batch", table_name="merge_decisions")
    op.drop_index("ix_merge_decisions_session_target", table_name="merge_decisions")
    op.drop_index(
        "ix_merge_decisions_session_id_decided_at",
        table_name="merge_decisions",
    )
    op.drop_table("merge_decisions")

    op.drop_index(
        "ix_merge_apply_batches_session_id_applied_at",
        table_name="merge_apply_batches",
    )
    op.drop_table("merge_apply_batches")

    op.drop_index("ix_merge_sessions_right_ref", table_name="merge_sessions")
    op.drop_index("ix_merge_sessions_left_ref", table_name="merge_sessions")
    op.drop_index(
        "ix_merge_sessions_user_status_last_active",
        table_name="merge_sessions",
    )
    op.drop_table("merge_sessions")
