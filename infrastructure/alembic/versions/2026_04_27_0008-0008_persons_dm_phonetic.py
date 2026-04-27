"""Persons Daitch-Mokotoff phonetic columns + GIN indexes (Phase 4.4.1).

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-27

Adds:
- ``persons.surname_dm`` — TEXT[] DM-кодов для всех фамилий персоны.
- ``persons.given_name_dm`` — TEXT[] DM-кодов для всех личных имён персоны.
- ``persons_surname_dm_gin`` — GIN-индекс под operator ``&&`` (arrays overlap).
- ``persons_given_name_dm_gin`` — GIN-индекс под ``&&``.

Заполнение колонок:
- На свежих импортах (``import_runner``) — синхронно при INSERT'е персон.
- На исторических данных — через ``scripts/backfill_dm_buckets.py``.

NULL означает «ещё не считали» (например, ряд импортирован до этой миграции
и backfill не запускался). Phonetic-search в API явно отсекает NULL'ы и
никогда не возвращает их без opt-in.

См. ``docs/agent-briefs/phase-4-4-1-phonetic-search.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add DM-bucket columns and GIN indexes."""
    op.add_column(
        "persons",
        sa.Column("surname_dm", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "persons",
        sa.Column("given_name_dm", postgresql.ARRAY(sa.String()), nullable=True),
    )
    # GIN покрывает operator `&&` — arrays overlap. Для phonetic-поиска
    # `WHERE surname_dm && ARRAY['463950']::text[]` это даёт O(log N)
    # вместо seq scan на 12k+ персонах.
    op.create_index(
        "persons_surname_dm_gin",
        "persons",
        ["surname_dm"],
        postgresql_using="gin",
    )
    op.create_index(
        "persons_given_name_dm_gin",
        "persons",
        ["given_name_dm"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Drop DM columns and indexes."""
    op.drop_index("persons_given_name_dm_gin", table_name="persons")
    op.drop_index("persons_surname_dm_gin", table_name="persons")
    op.drop_column("persons", "given_name_dm")
    op.drop_column("persons", "surname_dm")
