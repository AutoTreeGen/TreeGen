"""archive_listings + seed (Phase 22.1 / ADR-0074).

Revision ID: 0038
Revises: 0037
Create Date: 2026-05-03

Off-catalog archive registry: SBU/MVD/ZAGS/Standesamt/AGAD/military
caталог по country + record_type. Foundation для 22.2 (paid intermediary
directory), 22.3 (request-letter generator), 22.4 (cost dashboard).

Seed-данные грузятся в миграции (одна транзакция со схемой), источник —
``infrastructure/seed/archive_listings.json``. Pattern зеркалит
``DocumentTypeWeight`` seed в Phase 22.5 (0033) — упрощает контроль
версий: schema + initial data в одном PR, без отдельного data-migration
шага в деплой-пайплайне.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Путь к seed-файлу относительно корня репо. Миграции живут в
# infrastructure/alembic/versions/, поэтому два уровня вверх.
_SEED_PATH = (
    Path(__file__).resolve().parents[3] / "infrastructure" / "seed" / "archive_listings.json"
)


def upgrade() -> None:
    """Create archive_listings + load curated seed."""
    op.create_table(
        "archive_listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_native", sa.String(255), nullable=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("region", sa.String(128), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.String(254), nullable=True),
        sa.Column("contact_phone", sa.String(64), nullable=True),
        sa.Column("website", sa.String(512), nullable=True),
        sa.Column(
            "languages",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "record_types",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("year_from", sa.Integer(), nullable=True),
        sa.Column("year_to", sa.Integer(), nullable=True),
        sa.Column(
            "access_mode",
            sa.String(32),
            nullable=False,
            server_default="paid_request",
        ),
        sa.Column("fee_min_usd", sa.Integer(), nullable=True),
        sa.Column("fee_max_usd", sa.Integer(), nullable=True),
        sa.Column("typical_response_days", sa.Integer(), nullable=True),
        sa.Column("privacy_window_years", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_verified", sa.Date(), nullable=False),
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
            "country ~ '^[A-Z]{2}$'",
            name="ck_archive_listings_country_iso2",
        ),
        sa.CheckConstraint(
            "year_from IS NULL OR year_to IS NULL OR year_to >= year_from",
            name="ck_archive_listings_year_range",
        ),
        sa.CheckConstraint(
            "fee_min_usd IS NULL OR fee_max_usd IS NULL OR fee_max_usd >= fee_min_usd",
            name="ck_archive_listings_fee_range",
        ),
        sa.CheckConstraint(
            "fee_min_usd IS NULL OR fee_min_usd >= 0",
            name="ck_archive_listings_fee_min_nonneg",
        ),
        sa.CheckConstraint(
            "fee_max_usd IS NULL OR fee_max_usd >= 0",
            name="ck_archive_listings_fee_max_nonneg",
        ),
        sa.CheckConstraint(
            "typical_response_days IS NULL OR typical_response_days >= 0",
            name="ck_archive_listings_response_days_nonneg",
        ),
        sa.CheckConstraint(
            "privacy_window_years IS NULL OR privacy_window_years >= 0",
            name="ck_archive_listings_privacy_window_nonneg",
        ),
        sa.CheckConstraint(
            "access_mode IN ("
            "'online_catalog', 'in_person_only', 'paid_request', "
            "'intermediary_required', 'closed'"
            ")",
            name="ck_archive_listings_access_mode",
        ),
    )
    op.create_index("ix_archive_listings_country", "archive_listings", ["country"])
    op.create_index(
        "ix_archive_listings_country_access",
        "archive_listings",
        ["country", "access_mode"],
    )
    op.create_index(
        "ix_archive_listings_record_types_gin",
        "archive_listings",
        ["record_types"],
        postgresql_using="gin",
    )

    _seed_initial_listings()


def _seed_initial_listings() -> None:
    """Загрузить curated entries из JSON в archive_listings.

    Сделано как inline-вставка (не bulk_insert + ORM-mapping), чтобы
    миграция не зависела от текущей формы ORM-класса. Если ORM-shape
    позже сменится, эта миграция всё равно проиграется одинаково.
    """
    if not _SEED_PATH.exists():
        # Тесты могут гонять миграции из чистого worktree без seed-файла.
        # Лучше не падать, чем требовать опциональный seed.
        return

    raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        return

    rows: list[dict[str, object]] = []
    for entry in raw:
        last_verified_str = entry.get("last_verified")
        if not last_verified_str:
            # Anti-drift: запись без last_verified — пропускаем (не выдумываем).
            continue
        rows.append(
            {
                "id": uuid4(),
                "name": entry["name"],
                "name_native": entry.get("name_native"),
                "country": entry["country"],
                "region": entry.get("region"),
                "address": entry.get("address"),
                "contact_email": entry.get("contact_email"),
                "contact_phone": entry.get("contact_phone"),
                "website": entry.get("website"),
                "languages": entry.get("languages") or [],
                "record_types": entry.get("record_types") or [],
                "year_from": entry.get("year_from"),
                "year_to": entry.get("year_to"),
                "access_mode": entry.get("access_mode", "paid_request"),
                "fee_min_usd": entry.get("fee_min_usd"),
                "fee_max_usd": entry.get("fee_max_usd"),
                "typical_response_days": entry.get("typical_response_days"),
                "privacy_window_years": entry.get("privacy_window_years"),
                "notes": entry.get("notes"),
                "last_verified": date.fromisoformat(last_verified_str),
            }
        )

    if not rows:
        return

    archive_listings = sa.table(
        "archive_listings",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String(255)),
        sa.column("name_native", sa.String(255)),
        sa.column("country", sa.String(2)),
        sa.column("region", sa.String(128)),
        sa.column("address", sa.Text()),
        sa.column("contact_email", sa.String(254)),
        sa.column("contact_phone", sa.String(64)),
        sa.column("website", sa.String(512)),
        sa.column("languages", postgresql.JSONB()),
        sa.column("record_types", postgresql.JSONB()),
        sa.column("year_from", sa.Integer()),
        sa.column("year_to", sa.Integer()),
        sa.column("access_mode", sa.String(32)),
        sa.column("fee_min_usd", sa.Integer()),
        sa.column("fee_max_usd", sa.Integer()),
        sa.column("typical_response_days", sa.Integer()),
        sa.column("privacy_window_years", sa.Integer()),
        sa.column("notes", sa.Text()),
        sa.column("last_verified", sa.Date()),
    )
    op.bulk_insert(archive_listings, rows)


def downgrade() -> None:
    """Drop archive_listings (incl. seed)."""
    op.drop_index(
        "ix_archive_listings_record_types_gin",
        table_name="archive_listings",
    )
    op.drop_index("ix_archive_listings_country_access", table_name="archive_listings")
    op.drop_index("ix_archive_listings_country", table_name="archive_listings")
    op.drop_table("archive_listings")
