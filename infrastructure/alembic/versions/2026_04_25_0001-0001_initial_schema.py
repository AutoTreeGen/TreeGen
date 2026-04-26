"""Initial schema (Phase 2 MVP).

Revision ID: 0001
Revises:
Create Date: 2026-04-25

Создаёт MVP-схему AutoTreeGen:
- управление: users, trees, tree_collaborators, import_jobs, audit_log, versions;
- сущности: persons, names, families, family_children, events, event_participants,
  places, place_aliases, sources, citations, notes, entity_notes,
  multimedia_objects, entity_multimedia.

Не создаёт: pgvector-таблицы, dna-таблицы, hypotheses — отдельные миграции в своих
фазах. Расширения (vector, pg_trgm, uuid-ossp, pgcrypto, unaccent) уже подняты
init-скриптом docker-entrypoint-initdb.d/01-extensions.sql; оставляем
``CREATE EXTENSION IF NOT EXISTS`` для безопасной повторной накатки на чужой
кластер.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать всю MVP-схему."""
    # ---- расширения (idempotent) -----------------------------------------
    for ext in ("vector", "pg_trgm", "uuid-ossp", "pgcrypto", "unaccent"):
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')

    # ---- users -----------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("external_auth_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("external_auth_id", name="uq_users_external_auth_id"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_external_auth_id", "users", ["external_auth_id"])

    # ---- trees -----------------------------------------------------------
    op.create_table(
        "trees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="private"),
        sa.Column("default_locale", sa.String(8), nullable=False, server_default="en"),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_trees_owner_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_trees_owner_user_id", "trees", ["owner_user_id"])

    # ---- tree_collaborators ----------------------------------------------
    op.create_table(
        "tree_collaborators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="viewer"),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_tree_collaborators_tree_id_trees",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_tree_collaborators_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tree_id", "user_id", name="uq_tree_collaborators_tree_id_user_id"),
    )
    op.create_index("ix_tree_collaborators_tree_id", "tree_collaborators", ["tree_id"])
    op.create_index("ix_tree_collaborators_user_id", "tree_collaborators", ["user_id"])

    # ---- import_jobs -----------------------------------------------------
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_kind", sa.String(32), nullable=False, server_default="gedcom"),
        sa.Column("source_filename", sa.String(512), nullable=True),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_import_jobs_tree_id_trees", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_import_jobs_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "tree_id", "source_sha256", name="uq_import_jobs_tree_id_source_sha256"
        ),
    )
    op.create_index("ix_import_jobs_tree_id", "import_jobs", ["tree_id"])
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"])
    op.create_index("ix_import_jobs_source_sha256", "import_jobs", ["source_sha256"])

    # ---- persons ---------------------------------------------------------
    op.create_table(
        "persons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gedcom_xref", sa.String(64), nullable=True),
        sa.Column("sex", sa.String(2), nullable=False, server_default="U"),
        sa.Column("merged_into_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_persons_tree_id_trees", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_person_id"],
            ["persons.id"],
            name="fk_persons_merged_into_person_id_persons",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_persons_tree_id", "persons", ["tree_id"])
    op.create_index("ix_persons_gedcom_xref", "persons", ["gedcom_xref"])
    op.create_index("ix_persons_tree_id_deleted_at", "persons", ["tree_id", "deleted_at"])

    # ---- names -----------------------------------------------------------
    op.create_table(
        "names",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Free-form: реальные GEDCOM содержат AKA-конкатенации, длинные suffix'ы
        # с биографическими хвостами и т.п. — лимиты не нужны.
        sa.Column("given_name", sa.String(), nullable=True),
        sa.Column("surname", sa.String(), nullable=True),
        sa.Column("prefix", sa.String(), nullable=True),
        sa.Column("suffix", sa.String(), nullable=True),
        sa.Column("nickname", sa.String(), nullable=True),
        sa.Column("patronymic", sa.String(), nullable=True),
        sa.Column("maiden_surname", sa.String(), nullable=True),
        sa.Column("name_type", sa.String(32), nullable=False, server_default="birth"),
        sa.Column("script", sa.String(32), nullable=True),
        sa.Column("romanized", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"], ["persons.id"], name="fk_names_person_id_persons", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_names_person_id", "names", ["person_id"])
    op.create_index("ix_names_romanized", "names", ["romanized"])
    # GIN-индекс на romanized для fuzzy-поиска через pg_trgm.
    op.execute("CREATE INDEX ix_names_romanized_trgm ON names USING gin (romanized gin_trgm_ops)")

    # ---- families --------------------------------------------------------
    op.create_table(
        "families",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gedcom_xref", sa.String(64), nullable=True),
        sa.Column("husband_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("wife_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_families_tree_id_trees", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["husband_id"],
            ["persons.id"],
            name="fk_families_husband_id_persons",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["wife_id"], ["persons.id"], name="fk_families_wife_id_persons", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_families_tree_id", "families", ["tree_id"])
    op.create_index("ix_families_gedcom_xref", "families", ["gedcom_xref"])
    op.create_index("ix_families_husband_id", "families", ["husband_id"])
    op.create_index("ix_families_wife_id", "families", ["wife_id"])

    # ---- family_children -------------------------------------------------
    op.create_table(
        "family_children",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(16), nullable=False, server_default="biological"),
        sa.Column("birth_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["families.id"],
            name="fk_family_children_family_id_families",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["child_person_id"],
            ["persons.id"],
            name="fk_family_children_child_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "family_id", "child_person_id", name="uq_family_children_family_id_child_person_id"
        ),
    )
    op.create_index("ix_family_children_family_id", "family_children", ["family_id"])
    op.create_index("ix_family_children_child_person_id", "family_children", ["child_person_id"])

    # ---- places ----------------------------------------------------------
    op.create_table(
        "places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_name", sa.String(), nullable=False),
        sa.Column("country_code_iso", sa.String(8), nullable=True),
        sa.Column("admin1", sa.String(), nullable=True),
        sa.Column("admin2", sa.String(), nullable=True),
        sa.Column("settlement", sa.String(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("historical_period_start", sa.Date(), nullable=True),
        sa.Column("historical_period_end", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_places_tree_id_trees", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_places_tree_id", "places", ["tree_id"])
    op.create_index("ix_places_canonical_name", "places", ["canonical_name"])
    op.execute(
        "CREATE INDEX ix_places_canonical_name_trgm ON places USING gin (canonical_name gin_trgm_ops)"
    )

    # ---- place_aliases ---------------------------------------------------
    op.create_table(
        "place_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("script", sa.String(32), nullable=True),
        sa.Column("romanized", sa.String(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["place_id"], ["places.id"], name="fk_place_aliases_place_id_places", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_place_aliases_place_id", "place_aliases", ["place_id"])
    op.create_index("ix_place_aliases_romanized", "place_aliases", ["romanized"])

    # ---- events ----------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("custom_type", sa.String(), nullable=True),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("date_raw", sa.String(), nullable=True),
        sa.Column("date_start", sa.Date(), nullable=True),
        sa.Column("date_end", sa.Date(), nullable=True),
        sa.Column("date_qualifier", sa.String(16), nullable=True),
        sa.Column("date_calendar", sa.String(16), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_events_tree_id_trees", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["place_id"], ["places.id"], name="fk_events_place_id_places", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "(event_type = 'CUSTOM' AND custom_type IS NOT NULL) OR event_type <> 'CUSTOM'",
            name="ck_events_custom_type_required_for_custom",
        ),
    )
    op.create_index("ix_events_tree_id", "events", ["tree_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_place_id", "events", ["place_id"])
    op.create_index("ix_events_event_type_date_start", "events", ["event_type", "date_start"])

    # ---- event_participants ----------------------------------------------
    op.create_table(
        "event_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="principal"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_participants_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name="fk_event_participants_person_id_persons",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["families.id"],
            name="fk_event_participants_family_id_families",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(person_id IS NOT NULL) OR (family_id IS NOT NULL)",
            name="ck_event_participants_participant_must_be_person_or_family",
        ),
    )
    op.create_index("ix_event_participants_event_id", "event_participants", ["event_id"])
    op.create_index("ix_event_participants_person_id", "event_participants", ["person_id"])
    op.create_index("ix_event_participants_family_id", "event_participants", ["family_id"])

    # ---- sources ---------------------------------------------------------
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=True),
        sa.Column("publication", sa.String(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("repository", sa.String(), nullable=True),
        sa.Column("repository_id", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_sources_tree_id_trees", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_sources_tree_id", "sources", ["tree_id"])

    # ---- citations -------------------------------------------------------
    op.create_table(
        "citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_or_section", sa.String(255), nullable=True),
        sa.Column("quoted_text", sa.String(), nullable=True),
        sa.Column("quality", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_citations_tree_id_trees", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_citations_source_id_sources", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_citations_tree_id", "citations", ["tree_id"])
    op.create_index("ix_citations_source_id", "citations", ["source_id"])
    op.create_index("ix_citations_entity_type_entity_id", "citations", ["entity_type", "entity_id"])

    # ---- notes -----------------------------------------------------------
    op.create_table(
        "notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False, server_default="text/plain"),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_notes_tree_id_trees", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_notes_tree_id", "notes", ["tree_id"])

    # ---- entity_notes ----------------------------------------------------
    op.create_table(
        "entity_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["note_id"], ["notes.id"], name="fk_entity_notes_note_id_notes", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_entity_notes_note_id", "entity_notes", ["note_id"])
    op.create_index("ix_entity_notes_entity_id", "entity_notes", ["entity_id"])

    # ---- multimedia_objects ---------------------------------------------
    op.create_table(
        "multimedia_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_type", sa.String(16), nullable=False, server_default="image"),
        sa.Column("storage_url", sa.String(2048), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column("taken_date", sa.Date(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="probable"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version_id", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tree_id"],
            ["trees.id"],
            name="fk_multimedia_objects_tree_id_trees",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_multimedia_objects_tree_id", "multimedia_objects", ["tree_id"])
    op.create_index("ix_multimedia_objects_sha256", "multimedia_objects", ["sha256"])

    # ---- entity_multimedia ----------------------------------------------
    op.create_table(
        "entity_multimedia",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("multimedia_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="primary"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["multimedia_id"],
            ["multimedia_objects.id"],
            name="fk_entity_multimedia_multimedia_id_multimedia_objects",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_entity_multimedia_multimedia_id", "entity_multimedia", ["multimedia_id"])
    op.create_index("ix_entity_multimedia_entity_id", "entity_multimedia", ["entity_id"])

    # ---- audit_log -------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_kind", sa.String(32), nullable=False, server_default="system"),
        sa.Column("import_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_audit_log_tree_id_trees", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_log_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name="fk_audit_log_import_job_id_import_jobs",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_audit_log_tree_created", "audit_log", ["tree_id", "created_at"])
    op.create_index(
        "ix_audit_log_entity_created", "audit_log", ["entity_type", "entity_id", "created_at"]
    )

    # ---- versions --------------------------------------------------------
    op.create_table(
        "versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tree_id"], ["trees.id"], name="fk_versions_tree_id_trees", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_versions_created_by_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_versions_tree_entity", "versions", ["tree_id", "entity_type", "entity_id", "created_at"]
    )


def downgrade() -> None:
    """Откат: удалить все таблицы в обратном порядке зависимостей."""
    for table in (
        "versions",
        "audit_log",
        "entity_multimedia",
        "multimedia_objects",
        "entity_notes",
        "notes",
        "citations",
        "sources",
        "event_participants",
        "events",
        "place_aliases",
        "places",
        "family_children",
        "families",
        "names",
        "persons",
        "import_jobs",
        "tree_collaborators",
        "trees",
        "users",
    ):
        op.drop_table(table)
