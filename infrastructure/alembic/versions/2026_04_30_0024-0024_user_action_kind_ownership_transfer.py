"""User action kind: add ownership_transfer (Phase 4.11c).

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-30

Расширяет ``ck_user_action_requests_kind`` CHECK constraint, чтобы
``user_action_requests.kind`` принимал ещё одно значение —
``"ownership_transfer"``. Используется Phase 4.11c (см. ADR-0050):
auto-transfer worker создаёт по одному request-row на каждое shared
tree user'а перед erasure, чтобы next-eligible editor стал owner'ом.

Sequencing (post-rebase, 2026-04-30): #138 (public_tree_shares)
зафиксировал 0022 на main, #135 (Phase 4.11b user_action_status_manual)
лёг 0023. Эта миграция, изначально планировавшаяся как 0022, после
rebase'а отрезалась на 0024 / down_revision=0023.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop old CHECK + add new CHECK с дополнительным значением."""
    # Postgres не поддерживает ALTER CONSTRAINT для изменения CHECK-выражения,
    # поэтому drop+add. На пустой prod-таблице это мгновенно; на больших —
    # требует table-level ACCESS EXCLUSIVE lock на момент rewrite,
    # но user_action_requests мала по дизайну (один-два row на user).
    op.drop_constraint("ck_user_action_requests_kind", "user_action_requests", type_="check")
    op.create_check_constraint(
        "ck_user_action_requests_kind",
        "user_action_requests",
        "kind IN ('export', 'erasure', 'ownership_transfer')",
    )


def downgrade() -> None:
    """Откат: убрать ``ownership_transfer`` из allowed values.

    Lossy для row'ов с этим kind'ом — DELETE их перед constraint'ом,
    чтобы не получить CHECK violation на existing data.
    """
    op.execute("DELETE FROM user_action_requests WHERE kind = 'ownership_transfer'")
    op.drop_constraint("ck_user_action_requests_kind", "user_action_requests", type_="check")
    op.create_check_constraint(
        "ck_user_action_requests_kind",
        "user_action_requests",
        "kind IN ('export', 'erasure')",
    )
