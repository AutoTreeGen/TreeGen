"""Phase 3.6 — extend Source + Citation with GEDCOM SOURCE_CITATION sub-tags.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-27

Phase 1.x exposed PAGE / QUAY / EVEN / ROLE / ABBR / TEXT through the
``gedcom_parser`` entity API; Phase 3.6 persists them in the database so
they become first-class evidence (CLAUDE.md §3 — evidence-first).

Adds:

* ``sources.gedcom_xref``    — оригинальный xref (``S1``, без ``@``)
                                для дедупликации повторных импортов.
* ``sources.abbreviation``   — GEDCOM ABBR (часто единственный
                                идентификатор у Geni / FTM-экспортов).
* ``sources.text_excerpt``   — GEDCOM TEXT (если был встроен в SOUR).
* ``citations.quay_raw``     — сырой GEDCOM QUAY 0..3 (NULL — не задан).
* ``citations.event_type``   — подтег EVEN (какое событие подтверждает).
* ``citations.role``         — подтег EVEN > ROLE (роль персоны в нём).
* ``ck_citations_quay_raw_range`` — CHECK 0..3 на ``quay_raw``.
* ``ix_sources_gedcom_xref`` — частый lookup при идемпотентном импорте.
* ``ix_citations_entity``    — composite полиморфный индекс
                                ``(entity_type, entity_id)``: trees API
                                подтягивает citations для batch event-id.

Идемпотентность: миграция чисто аддитивная — всё ``ADD COLUMN`` /
``CREATE INDEX``. Существующие строки получают ``NULL`` в новых
nullable-полях. ``downgrade()`` снимает добавленные объекты в обратном
порядке.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add GEDCOM SOURCE_CITATION sub-tag columns + indices."""
    # ---- sources --------------------------------------------------------
    op.add_column(
        "sources",
        sa.Column("gedcom_xref", sa.String(length=64), nullable=True),
    )
    op.add_column("sources", sa.Column("abbreviation", sa.String(), nullable=True))
    op.add_column("sources", sa.Column("text_excerpt", sa.String(), nullable=True))
    op.create_index("ix_sources_gedcom_xref", "sources", ["gedcom_xref"])

    # ---- citations ------------------------------------------------------
    op.add_column("citations", sa.Column("quay_raw", sa.SmallInteger(), nullable=True))
    op.add_column("citations", sa.Column("event_type", sa.String(length=16), nullable=True))
    op.add_column("citations", sa.Column("role", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_citations_quay_raw_range",
        "citations",
        "quay_raw IS NULL OR (quay_raw >= 0 AND quay_raw <= 3)",
    )
    op.create_index("ix_citations_entity", "citations", ["entity_type", "entity_id"])


def downgrade() -> None:
    """Reverse upgrade in reverse order."""
    op.drop_index("ix_citations_entity", table_name="citations")
    op.drop_constraint("ck_citations_quay_raw_range", "citations", type_="check")
    op.drop_column("citations", "role")
    op.drop_column("citations", "event_type")
    op.drop_column("citations", "quay_raw")

    op.drop_index("ix_sources_gedcom_xref", table_name="sources")
    op.drop_column("sources", "text_excerpt")
    op.drop_column("sources", "abbreviation")
    op.drop_column("sources", "gedcom_xref")
