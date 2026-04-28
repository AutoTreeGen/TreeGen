"""waitlist_entries (Phase 4.12 / ADR-0035).

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-28

Добавляет таблицу для маркетингового lead-capture с лендинга:

* email (unique, indexed) — кому писать про релизы.
* locale — какой язык показывать в рассылке.
* source — откуда пришёл лид (Phase 4.12 — всегда «landing»;
  Phase 4.13 разделит utm-кампании).
* created_at — для retention-cohort анализа.

Без user_id / FK на users — лид-форма работает без логина (это её
суть). Если позже user зарегистрировался с тем же email, можно
матчить через email substring (ADR-0035 §«Linking leads to users»).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "waitlist_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("locale", sa.String(16), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="uq_waitlist_entries_email"),
    )
    op.create_index("ix_waitlist_entries_email", "waitlist_entries", ["email"])


def downgrade() -> None:
    op.drop_index("ix_waitlist_entries_email", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")
