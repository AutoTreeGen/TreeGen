"""Self-anchor: ``trees.owner_person_id`` (Phase 10.7a / ADR-0068).

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-01

Добавляет колонку ``trees.owner_person_id`` — позицию владельца внутри
собственного дерева. Без этого якоря AI-фичи (Phase 10.7b Context Pack,
10.7d Chat UI, 10.8 MCP server) «не знают, кто ты» и не могут вычислить
эго-родство ("брат жены" vs "брат тёщи").

Колонка nullable: дерево создаётся без anchor, владелец выбирает себя
явно через UI (PATCH /trees/{id}/owner-person). ``ON DELETE SET NULL`` —
если человек удалён из дерева, anchor сбрасывается автоматически (без
RESTRICT, чтобы не блокировать legitimate person-deletion-flow).

Partial index ``WHERE owner_person_id IS NOT NULL`` — большинство деревьев
до миграции имеют NULL; индекс маленький, обслуживает запросы вида
«найди все деревья этого человека» в рамках Phase 10.7d Chat UI.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавить ``trees.owner_person_id`` + partial index."""
    op.add_column(
        "trees",
        sa.Column(
            "owner_person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_trees_owner_person",
        "trees",
        ["owner_person_id"],
        postgresql_where=sa.text("owner_person_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Удалить partial index и колонку."""
    op.drop_index("ix_trees_owner_person", table_name="trees")
    op.drop_column("trees", "owner_person_id")
