"""Audit log: nullable tree_id + wider action + user-actions index (Phase 4.11a).

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-29

Расширяет ``audit_log`` для GDPR user-level action'ов (см. ADR-0046):

* ``tree_id`` становится nullable. User-инициированные GDPR-actions
  (export запрос, processing transitions, completion, failure, erasure
  request) не привязаны к конкретному дереву — они описывают действия
  над user-account'ом. Auto-listener
  (``shared_models.audit._make_audit_entry``) уже отфильтровывает
  объекты без ``tree_id``, поэтому tree-scoped семантика не нарушается;
  GDPR-rows вставляются вручную.
* ``action`` расширен с ``varchar(16)`` до ``varchar(32)``: новые значения
  ``"export_processing"`` (17) и ``"erasure_requested"`` (17) не помещаются
  в 16 символов.
* Партиал-индекс ``ix_audit_log_user_actions`` по
  ``(actor_user_id, action, created_at) WHERE tree_id IS NULL`` —
  ускоряет «история GDPR-events моего аккаунта». Партиал чтобы не
  раздувать индекс tree-scoped audit-записями (на 3+ порядка больше).

Downgrade сначала удаляет user-action rows (которые невозможно
ассоциировать с tree_id), потом возвращает NOT NULL и varchar(16).
Это lossy — даём warning в op.execute (logger недоступен в alembic context).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Сделать tree_id nullable, расширить action, добавить партиал-индекс."""
    op.alter_column(
        "audit_log",
        "tree_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "audit_log",
        "action",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )
    op.create_index(
        "ix_audit_log_user_actions",
        "audit_log",
        ["actor_user_id", "action", "created_at"],
        postgresql_where=sa.text("tree_id IS NULL"),
    )


def downgrade() -> None:
    """Откат: удалить user-action rows + вернуть NOT NULL/varchar(16).

    User-action audit-записи нельзя «вернуть» в tree-scoped схему —
    у них нет ассоциированного дерева. Поэтому downgrade удаляет
    их физически. На production-данных это не должно вызываться без
    backup'а.
    """
    op.drop_index(
        "ix_audit_log_user_actions",
        table_name="audit_log",
        postgresql_where=sa.text("tree_id IS NULL"),
    )
    # Lossy: удаляем GDPR-action rows перед сужением колонок.
    op.execute("DELETE FROM audit_log WHERE tree_id IS NULL")
    op.alter_column(
        "audit_log",
        "action",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
    op.alter_column(
        "audit_log",
        "tree_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
