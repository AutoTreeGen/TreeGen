"""Тесты ORM моделей DnaConsent + DnaTestRecord (Phase 6.2 — ADR-0020).

Структурные проверки без БД: схема таблиц, отсутствие soft-delete /
provenance / version_id (DNA opt-out из ADR-0003), наличие нужных FK
и индексов. Поведенческие интеграционные тесты сервисного flow —
в `services/dna-service/tests/`.
"""

from __future__ import annotations

import pytest
from shared_models import Base
from shared_models.orm import DnaConsent, DnaTestRecord


def test_dna_consents_table_present() -> None:
    assert "dna_consents" in Base.metadata.tables


def test_dna_test_records_table_present() -> None:
    assert "dna_test_records" in Base.metadata.tables


@pytest.mark.parametrize("table_name", ["dna_consents", "dna_test_records"])
def test_dna_table_has_no_soft_delete(table_name: str) -> None:
    """Phase 6.2 / ADR-0020: DNA-таблицы opt-out из soft-delete (ADR-0003).

    Удаление DNA = hard delete + удаление файла + factum-only audit.
    """
    table = Base.metadata.tables[table_name]
    assert "deleted_at" not in table.c, (
        f"{table_name} must not have deleted_at — DNA uses hard delete"
    )


@pytest.mark.parametrize("table_name", ["dna_consents", "dna_test_records"])
def test_dna_table_has_no_provenance(table_name: str) -> None:
    """provenance бы рассказывал о происхождении — для consent / blob это
    утечка metadata, поэтому НЕ добавляем."""
    table = Base.metadata.tables[table_name]
    assert "provenance" not in table.c


@pytest.mark.parametrize("table_name", ["dna_consents", "dna_test_records"])
def test_dna_table_has_no_version_id(table_name: str) -> None:
    """version_id для consent/blob не нужен — записи immutable, кроме
    revoked_at flag."""
    table = Base.metadata.tables[table_name]
    assert "version_id" not in table.c


def test_dna_consents_columns() -> None:
    cols = Base.metadata.tables["dna_consents"].c
    expected = {
        "id",
        "tree_id",
        "user_id",
        "kit_owner_email",
        "consent_text",
        "consent_version",
        "consented_at",
        "revoked_at",
        "created_at",
    }
    assert set(cols.keys()) == expected


def test_dna_test_records_columns() -> None:
    cols = Base.metadata.tables["dna_test_records"].c
    expected = {
        "id",
        "tree_id",
        "consent_id",
        "user_id",
        "storage_path",
        "size_bytes",
        "sha256",
        "snp_count",
        "provider",
        "encryption_scheme",
        "uploaded_at",
        "created_at",
    }
    assert set(cols.keys()) == expected


def test_dna_test_records_has_consent_fk() -> None:
    """consent_id → dna_consents.id с RESTRICT (ADR-0020 §«Revocation flow»):
    сервис должен явно удалить blob + row, не полагаться на cascade."""
    fks = list(Base.metadata.tables["dna_test_records"].c["consent_id"].foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "dna_consents"
    assert fk.ondelete == "RESTRICT"


def test_dna_consent_is_active_property() -> None:
    """is_active возвращает True для не-отозванного, False для отозванного."""
    import datetime as dt

    consent = DnaConsent(
        tree_id=None,  # type: ignore[arg-type] — instance-only test, без save
        user_id=None,  # type: ignore[arg-type]
        kit_owner_email="user@example.com",
        consent_text="I consent",
        consent_version="1.0",
    )
    assert consent.is_active is True

    consent.revoked_at = dt.datetime.now(dt.UTC)
    assert consent.is_active is False


def test_dna_test_record_default_encryption_scheme() -> None:
    """Default encryption_scheme на ORM-уровне (server_default = 'none').

    Реальный default в Phase 6.2 fix через ALTER COLUMN не нужен —
    миграция уже ставит server_default 'none'. Этот тест документирует
    инвариант для регрессий.
    """
    col = Base.metadata.tables["dna_test_records"].c["encryption_scheme"]
    assert col.server_default is not None
    server_default_value = col.server_default.arg
    assert "none" in str(server_default_value)


def test_dna_test_record_kwargs_compose() -> None:
    """Sanity-check: можно сконструировать DnaTestRecord с минимальными
    kwargs (без БД-flush)."""
    import uuid as uuid_module

    record = DnaTestRecord(
        tree_id=uuid_module.uuid4(),
        consent_id=uuid_module.uuid4(),
        user_id=uuid_module.uuid4(),
        storage_path="dna/blob-uuid.bin",
        size_bytes=12345,
        sha256="0" * 64,
        snp_count=700_000,
        provider="23andme",
    )
    assert record.storage_path == "dna/blob-uuid.bin"
    assert record.snp_count == 700_000
    assert record.provider == "23andme"
