"""Evidence weight/provenance split — Phase 22.5 / ADR-0071.

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-02

Создаёт две таблицы:

* ``document_type_weights`` — lookup tier-веса по ``DocumentType``.
  Seed-данные: одна row на каждое значение enum'а; tier-mapping
  фиксируется здесь (не в Python). Update in-place — допустимый flow
  переоценки tier'а без деплоя (см. ADR-0071).
* ``evidence`` — off-catalog evidence-row: документ + provenance +
  derived confidence. Provenance — JSONB с *жёсткой* Pydantic-формой
  (channel, cost_usd, archive_name, …) — отдельно от ``document_type``
  («что это за документ»).

Обе таблицы новые: backfill для существующих row'ов не нужен (их нет).
``provenance`` server-default = ``{"channel":"unknown","migrated":true}``
гарантирует, что любая будущая INSERT без явного provenance попадёт под
backfill-семантику и UI/aggregations смогут отфильтровать.

ВАЖНО: при ручном INSERT нужно ставить либо явный channel, либо
полагаться на server_default; application-validator (Phase 22.x
endpoint работа) обязан отвергать ``channel == 'unknown'`` для
свежих записей.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tier-mapping для seed-данных. Tier-N → weight=N. Подробное обоснование
# выбора tier'а — ADR-0071. Список *exhaustive* по DocumentType: каждое
# enum-значение должно иметь row, иначе FK на evidence не сможет вставить.
_TIER_1: list[tuple[str, str]] = [
    ("passport", "National passport — primary government identity record."),
    ("birth_certificate", "Civil-registry birth certificate (primary record)."),
    ("death_certificate", "Civil-registry death certificate (primary record)."),
    ("marriage_certificate", "Civil-registry marriage certificate (primary record)."),
    ("divorce_record", "Civil-registry divorce decree (primary record)."),
    (
        "civil_register_extract",
        "Extract from civil registry (ZAGS / Standesamt / equivalent).",
    ),
    ("military_record", "Military service record / draft register entry."),
    ("naturalization", "Naturalization petition or certificate."),
    ("census_household", "Household census enumeration (line-level, not index)."),
    (
        "metric_book_entry",
        "Pre-1918 metric book entry (Russian Empire / Austro-Hungarian / similar).",
    ),
    (
        "revision_list_entry",
        "Russian Empire revision-list (ревизская сказка) entry.",
    ),
]

_TIER_2: list[tuple[str, str]] = [
    ("family_bible", "Family-bible inscription (private primary record)."),
    (
        "photograph_with_caption",
        "Captioned photograph identifying person, date, or place.",
    ),
    ("headstone_inscription", "Cemetery headstone inscription / cemetery register."),
    ("obituary", "Newspaper obituary or memorial notice."),
    (
        "immigration_passenger_list",
        "Ship or border immigration passenger manifest.",
    ),
]

_TIER_3: list[tuple[str, str]] = [
    ("gedcom_import", "Imported from another GEDCOM file (provenance derived)."),
    ("public_tree_copy", "Copied from a public online tree."),
    ("online_index_entry", "Entry from an online index (no original-image link)."),
    ("oral_testimony", "Oral testimony / family interview (recalled)."),
    ("family_letter", "Personal letter referencing the fact."),
    # DNA documents: weight stored here for completeness; Phase 16.x
    # has its own scoring pipeline that consumes shared cM / segment
    # data directly. См. ADR-0071 §«DNA out of scope».
    (
        "dna_match_segment",
        "DNA chromosome-segment match (Phase 16.x weights it separately).",
    ),
    (
        "dna_match_total_cm",
        "DNA total-shared-cM match (Phase 16.x weights it separately).",
    ),
    ("other", "Catch-all / backfill default."),
]


def upgrade() -> None:
    """Создать ``document_type_weights`` + ``evidence``, засеять lookup."""

    # ---- 1. Lookup-таблица tier-веса ------------------------------------
    op.create_table(
        "document_type_weights",
        sa.Column("document_type", sa.String(64), primary_key=True),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.CheckConstraint("weight IN (1, 2, 3)", name="ck_doc_type_weight_tier"),
    )

    # Seed: каждое значение DocumentType — отдельная row. Bulk insert
    # одним statement'ом, чтобы мигрейт был атомарен.
    weights_table = sa.table(
        "document_type_weights",
        sa.column("document_type", sa.String),
        sa.column("weight", sa.Integer),
        sa.column("description", sa.String),
    )
    rows: list[dict[str, object]] = []
    for dt_value, desc in _TIER_1:
        rows.append({"document_type": dt_value, "weight": 1, "description": desc})
    for dt_value, desc in _TIER_2:
        rows.append({"document_type": dt_value, "weight": 2, "description": desc})
    for dt_value, desc in _TIER_3:
        rows.append({"document_type": dt_value, "weight": 3, "description": desc})
    op.bulk_insert(weights_table, rows)

    # ---- 2. Evidence ----------------------------------------------------
    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_type",
            sa.String(64),
            sa.ForeignKey(
                "document_type_weights.document_type",
                ondelete="RESTRICT",
            ),
            nullable=False,
            server_default="other",
        ),
        sa.Column(
            "match_certainty",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.5"),
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("""'{"channel": "unknown", "migrated": true}'::jsonb"""),
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
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
        sa.CheckConstraint("confidence >= 0", name="ck_evidence_confidence_non_negative"),
        sa.CheckConstraint(
            "match_certainty >= 0 AND match_certainty <= 1",
            name="ck_evidence_match_certainty_range",
        ),
        sa.CheckConstraint(
            "provenance ? 'channel'",
            name="ck_evidence_provenance_has_channel",
        ),
    )
    op.create_index("ix_evidence_tree_id", "evidence", ["tree_id"])
    op.create_index(
        "ix_evidence_entity",
        "evidence",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_evidence_tree_confidence",
        "evidence",
        ["tree_id", "confidence"],
    )


def downgrade() -> None:
    """Удалить ``evidence`` (FK→sources/trees), затем lookup."""
    op.drop_index("ix_evidence_tree_confidence", table_name="evidence")
    op.drop_index("ix_evidence_entity", table_name="evidence")
    op.drop_index("ix_evidence_tree_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_table("document_type_weights")
