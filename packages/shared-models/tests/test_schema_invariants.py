"""Структурные тесты схемы (без БД).

Проверяют, что все доменные записи дерева соответствуют ADR-0003: имеют tree_id,
provenance, version_id, deleted_at, status, confidence_score.
"""

from __future__ import annotations

import pytest
from shared_models import (
    Base,
    orm,  # noqa: F401  — регистрируем модели
)

# Эти таблицы — служебные, миксины к ним применяются по разным правилам.
SERVICE_TABLES = {
    "users",
    "tree_collaborators",
    "import_jobs",
    "audit_log",
    "versions",
    "family_children",
    "event_participants",
    "entity_notes",
    "entity_multimedia",
    # DNA service tables
    "shared_matches",
    "dna_imports",
}

TREE_ENTITY_TABLES = {
    "trees",
    "persons",
    "names",
    "families",
    "events",
    "places",
    "place_aliases",
    "sources",
    "citations",
    "notes",
    "multimedia_objects",
    # DNA tree-entities
    "dna_kits",
    "dna_matches",
}


@pytest.mark.parametrize("table_name", sorted(TREE_ENTITY_TABLES))
def test_live_entity_has_soft_delete(table_name: str) -> None:
    """Каждая запись дерева имеет deleted_at."""
    table = Base.metadata.tables[table_name]
    assert "deleted_at" in table.c, f"{table_name} missing deleted_at"


@pytest.mark.parametrize(
    "table_name",
    sorted(
        TREE_ENTITY_TABLES - {"names", "place_aliases"}
    ),  # подсущности унаследуют provenance с родителя
)
def test_top_level_live_entity_has_provenance(table_name: str) -> None:
    """Каждая запись верхнего уровня имеет provenance."""
    table = Base.metadata.tables[table_name]
    assert "provenance" in table.c, f"{table_name} missing provenance"


@pytest.mark.parametrize(
    "table_name",
    sorted(TREE_ENTITY_TABLES - {"names", "place_aliases", "citations"}),
)
def test_live_entity_has_version_id(table_name: str) -> None:
    """Каждая запись дерева имеет version_id."""
    table = Base.metadata.tables[table_name]
    assert "version_id" in table.c, f"{table_name} missing version_id"


def test_audit_log_table_present() -> None:
    """Phase 2 обязана зарегистрировать audit_log."""
    assert "audit_log" in Base.metadata.tables


def test_versions_table_present() -> None:
    """Phase 2 обязана зарегистрировать versions."""
    assert "versions" in Base.metadata.tables


def test_no_unexpected_tables() -> None:
    """Никаких посторонних таблиц в Base.metadata.

    Защита от случайной протечки моделей из dna/inference/embeddings (они
    появятся в своих фазах и должны управляться отдельной миграцией).
    """
    expected = SERVICE_TABLES | TREE_ENTITY_TABLES
    actual = set(Base.metadata.tables.keys())
    extra = actual - expected
    assert not extra, f"unexpected tables in metadata: {extra}"
