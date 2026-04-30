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
    # DNA consent + storage records (Phase 6.2 / ADR-0020): explicitly
    # opt out of soft-delete and provenance — revocation is hard delete.
    "dna_consents",
    "dna_test_records",
    # Inference engine evidences (Phase 7.2 / ADR-0021): атомарные
    # доказательства, привязаны через FK CASCADE на hypothesis. Тоже
    # service-table — не несут soft-delete (удаление вместе с гипотезой).
    "hypothesis_evidences",
    # Manual person-merge audit (Phase 4.6 / ADR-0022): лог-trail с
    # собственной retention-политикой (90-дневное undo-окно + purge).
    # ``undone_at`` и ``purged_at`` — отдельные indicators событий,
    # не soft-delete этой строки.
    "person_merge_logs",
    # Notification deliveries (Phase 8.0 / ADR-0024): per-user, не
    # per-tree; нет soft-delete (idempotency-окно 1 час делает delete
    # неактуальным для re-send), нет provenance (источник — internal
    # callers через POST /notify, не GEDCOM/DNA-import).
    "notifications",
    # Per-user notification preferences (Phase 8.0 wire-up / ADR-0029):
    # composite-PK (user_id, event_type), нет soft-delete (отключение
    # модельируется флагом ``enabled``, не deletion), нет provenance —
    # это user setting, не доменный факт.
    "notification_preferences",
    # FS-flagged dedup attempts (Phase 5.2.1): timestamp-state log
    # ``(rejected_at, merged_at)`` без soft-delete; уникальность
    # активного состояния — partial unique индекс. См. ORM-модуль.
    "fs_dedup_attempts",
    # Bulk hypothesis-compute jobs (Phase 7.5): служебные job rows
    # с прогрессом и cancel-флагом. Soft-delete не нужен — старые job'ы
    # purge'аются retention-политикой (TBD).
    "hypothesis_compute_jobs",
    # Public landing waitlist (Phase 4.12 / ADR-0035): pre-launch email
    # capture, не доменная сущность дерева. Без tree_id и soft-delete —
    # одноразовая запись, идемпотентная по email; обработка через batch
    # export, не tombstone-recovery.
    "waitlist_entries",
    # Tree role memberships (Phase 11.0 / ADR-0036): per-user role assignment
    # для shared trees, не доменная сущность дерева. Без soft-delete —
    # revocation = hard delete; partial-unique-OWNER гарантирует уникального
    # владельца.
    "tree_memberships",
    # Sharing invitations (Phase 11.0 / ADR-0036): token-based one-time link
    # с TTL; expired/revoked indicators отдельные timestamp-state, не
    # soft-delete.
    "tree_invitations",
    # Email send audit log (Phase 12.2): provider-side immutable record
    # для idempotent dispatch (idempotency_key UNIQUE) и debug. Без soft-delete —
    # immutable history, retention через batch purge.
    "email_send_log",
    # Telegram chat linking (Phase 14.0 / ADR-0040): per-user opt-in link к
    # Telegram chat. Service-level mapping, без tree_id и soft-delete —
    # revocation = revoked_at timestamp, не tombstone.
    "telegram_user_links",
    # User-initiated GDPR/account requests (Phase 4.10b → processed in 4.11):
    # erasure/export request log с status state machine. Service-level log,
    # не доменная сущность дерева.
    "user_action_requests",
    # Public tree shares (Phase 11.2 / ADR-0047): token-based public read-only
    # links к дереву. Sharing-artifact как tree_memberships/tree_invitations,
    # не доменная сущность — не требует provenance/version_id/soft-delete.
    "public_tree_shares",
    # AI source extraction run-log (Phase 10.2 / ADR-0059): per-vendor-call
    # cost tracking + raw_response для debug/analytics. Immutable history;
    # purge через retention policy (TBD), не tombstone.
    "source_extractions",
    # Per-fact suggestions из AI source extraction (Phase 10.2 / ADR-0059):
    # каждая Pydantic-модель из ExtractionResult сохраняется как одна row
    # с status pending|accepted|rejected. Audit-trail review-decisions,
    # не доменная сущность.
    "extracted_facts",
    # Stripe customer mapping (Phase 12.0 / ADR-0042): user → stripe_customer_id
    # one-to-one. Service-level mapping, без soft-delete — revocation = hard delete
    # после account deletion.
    "stripe_customers",
    # Subscription state (Phase 12.0 / ADR-0042): canonical billing state per user.
    # Мутируется ТОЛЬКО webhook'ами (никогда не application-side). Без soft-delete —
    # canceled — это status, не tombstone.
    "subscriptions",
    # Stripe webhook idempotency log (Phase 12.0 / ADR-0042): stripe_event_id UNIQUE
    # для idempotent dispatch. Audit trail, без soft-delete.
    "stripe_event_log",
    # Genealogy Git change proposals (Phase 16.1 / ADR-0062): PR-style
    # workflow log с собственной state machine
    # (open/approved/rejected/merged/rolled_back) — не доменная сущность
    # дерева, а audit/workflow record. Без provenance/version_id/
    # soft-delete (status — explicit machine, не tombstone).
    "tree_change_proposals",
    # Source-citation attachments к proposal'у (Phase 16.1 / ADR-0062):
    # many-to-many между proposals и sources с opaque relationship_ref
    # jsonb. Audit-trail evidence, не tree-entity.
    "tree_change_proposal_evidence",
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
    # Inference engine hypotheses (Phase 7.2 / ADR-0021): TreeEntityMixins
    # → имеют tree_id, soft-delete, provenance, version_id, status,
    # confidence_score (в дополнение к специфичному composite_score).
    "hypotheses",
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
