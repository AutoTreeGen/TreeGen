"""user_action_requests: add 'manual_intervention_required' status (Phase 4.11b).

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-29

Renumbered from 0022 → 0023 to yield 0022 to PR #138 (public_tree_shares),
which merged first.

Erasure worker (Phase 4.11b, ADR-0049) использует промежуточный
терминальный статус ``manual_intervention_required`` для блокирующих
edge-case'ов (shared tree с другими members, pending export request,
active subscription без cancellation hook'а). От ``failed`` отличается
семантически: ``failed`` означает «code-side error, retry safe»,
``manual_intervention_required`` — «состояние БД требует вмешательства
admin'а / Phase 4.11c ownership-transfer flow'а перед retry».

Расширяет CHECK-constraint ``ck_user_action_requests_status``:

* старое: ``status IN ('pending', 'processing', 'done', 'failed', 'cancelled')``
* новое: добавляется ``'manual_intervention_required'``

Downgrade удаляет rows с этим статусом (lossy) перед сужением
constraint'а — иначе CHECK не сработает на legacy-rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_STATUSES = "'pending', 'processing', 'done', 'failed', 'cancelled'"
_NEW_STATUSES = (
    "'pending', 'processing', 'done', 'failed', 'cancelled', 'manual_intervention_required'"
)
_CONSTRAINT = "ck_user_action_requests_status"
_TABLE = "user_action_requests"


def upgrade() -> None:
    """Расширить CHECK на ``status``: добавить ``manual_intervention_required``."""
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"status IN ({_NEW_STATUSES})")


def downgrade() -> None:
    """Откат: удалить manual-intervention rows + сузить CHECK обратно."""
    # Lossy: rows с этим статусом не fit'ятся в old-set; удаляем перед
    # пересозданием constraint'а.
    op.execute("DELETE FROM user_action_requests WHERE status = 'manual_intervention_required'")
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"status IN ({_OLD_STATUSES})")
