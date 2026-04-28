"""Audit-log invariants для consent + DNA test record lifecycle (ADR-0012, ADR-0020).

DnaConsent / DnaTestRecord не подключены к ``register_audit_listeners``
(ADR-0012 — DNA opts out of ADR-0003), поэтому консент-эндпоинты
пишут audit_log записи **явно**. Этот файл проверяет, что:

    - Создание consent → ровно одна audit_log запись (action=insert).
    - Revoke consent (без привязанных blob'ов) → ровно одна audit_log
      запись (action=update, diff содержит revoked_at).
    - Revoke consent с blob'ами → audit_log содержит UPDATE на
      consent + DELETE на каждый DnaTestRecord. Каждый delete-row
      factum-only: без storage_path / sha256 / consent_id в diff
      (associativity-erasure per DnaTestRecord ORM docstring).
    - Идемпотентный повторный revoke не плодит лишних записей.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from shared_models.enums import ActorKind, AuditAction
from shared_models.orm import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SYNTHETIC_23ANDME = (
    _REPO_ROOT / "packages" / "dna-analysis" / "tests" / "fixtures" / "synthetic_23andme.txt"
)


@pytest_asyncio.fixture
async def db_session(postgres_dsn: str) -> AsyncIterator[AsyncSession]:
    """Прямой read-only-ish session к test-DB для проверки audit_log.

    Используем отдельный engine от того, что висит на app — чтобы
    избежать коллизий transactional state. Только для select'ов.
    """
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _audit_rows_for(
    session: AsyncSession, *, entity_type: str, entity_id: uuid.UUID
) -> list[AuditLog]:
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == entity_type)
                .where(AuditLog.entity_id == entity_id)
                .order_by(AuditLog.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _consent_payload(user_id: uuid.UUID, tree_id: uuid.UUID, **overrides: Any) -> dict[str, str]:
    payload = {
        "tree_id": str(tree_id),
        "user_id": str(user_id),
        "kit_owner_email": "owner@example.com",
        "consent_text": "I consent",
    }
    payload.update({k: str(v) for k, v in overrides.items()})
    return payload


@pytest.mark.db
@pytest.mark.integration
async def test_audit_log_records_consent_creation(
    app_client, seeded_user_and_tree, db_session
) -> None:
    """Создание consent → одна audit-запись с action=insert и кор. actor."""
    user_id, tree_id = seeded_user_and_tree

    create_resp = await app_client.post("/consents", json=_consent_payload(user_id, tree_id))
    assert create_resp.status_code == 201
    consent_id = uuid.UUID(create_resp.json()["id"])

    rows = await _audit_rows_for(db_session, entity_type="dna_consents", entity_id=consent_id)
    assert len(rows) == 1
    entry = rows[0]
    assert entry.action == AuditAction.INSERT.value
    assert entry.actor_kind == ActorKind.USER.value
    assert entry.actor_user_id == user_id
    assert entry.tree_id == tree_id
    assert entry.reason == "consent.create"

    # Diff должен включать non-PII fields, но НЕ kit_owner_email и НЕ consent_text.
    after = entry.diff["after"]
    assert "kit_owner_email" not in after
    assert "consent_text" not in after
    assert after["user_id"] == str(user_id)
    assert after["tree_id"] == str(tree_id)
    assert after["consent_version"] == "1.0"


@pytest.mark.db
@pytest.mark.integration
async def test_audit_log_records_consent_revocation_without_blobs(
    app_client, seeded_user_and_tree, db_session
) -> None:
    """Revoke consent без привязанных blob'ов → одна UPDATE-запись."""
    user_id, tree_id = seeded_user_and_tree

    create_resp = await app_client.post("/consents", json=_consent_payload(user_id, tree_id))
    consent_id = uuid.UUID(create_resp.json()["id"])

    revoke_resp = await app_client.delete(f"/consents/{consent_id}")
    assert revoke_resp.status_code == 204

    rows = await _audit_rows_for(db_session, entity_type="dna_consents", entity_id=consent_id)
    # INSERT + UPDATE.
    assert len(rows) == 2
    insert_row, update_row = rows
    assert insert_row.action == AuditAction.INSERT.value
    assert update_row.action == AuditAction.UPDATE.value
    assert update_row.reason == "consent.revoke"
    assert update_row.actor_kind == ActorKind.USER.value
    assert update_row.actor_user_id == user_id
    assert update_row.diff["fields"] == ["revoked_at"]
    assert update_row.diff["changes"]["revoked_at"]["before"] is None
    assert update_row.diff["changes"]["revoked_at"]["after"] is not None


@pytest.mark.db
@pytest.mark.integration
async def test_audit_log_records_each_test_record_hard_delete_factum_only(
    app_client, seeded_user_and_tree, db_session
) -> None:
    """Каждый каскадный hard-delete DnaTestRecord → factum-only audit-запись.

    Per DnaTestRecord ORM docstring: «без kit_id / sha256 / storage_path
    в audit, чтобы deletion действительно стирала associativity».
    """
    user_id, tree_id = seeded_user_and_tree

    create_resp = await app_client.post("/consents", json=_consent_payload(user_id, tree_id))
    consent_id = uuid.UUID(create_resp.json()["id"])

    with _SYNTHETIC_23ANDME.open("rb") as fh:
        upload_resp = await app_client.post(
            "/dna-uploads",
            data={"consent_id": str(consent_id)},
            files={"file": (_SYNTHETIC_23ANDME.name, fh, "text/plain")},
        )
    assert upload_resp.status_code == 201
    record_id = uuid.UUID(upload_resp.json()["id"])
    record_sha256 = upload_resp.json()["sha256"]

    revoke_resp = await app_client.delete(f"/consents/{consent_id}")
    assert revoke_resp.status_code == 204

    record_audit = await _audit_rows_for(
        db_session, entity_type="dna_test_records", entity_id=record_id
    )
    assert len(record_audit) == 1
    entry = record_audit[0]
    assert entry.action == AuditAction.DELETE.value
    assert entry.reason == "consent.revoke.cascade_test_record"
    assert entry.actor_kind == ActorKind.USER.value
    assert entry.actor_user_id == user_id
    assert entry.tree_id == tree_id

    # Factum-only diff: НЕТ storage_path, sha256, consent_id, snp_count и т.п.
    diff = entry.diff
    assert diff.get("factum") == "hard_delete"
    assert diff.get("fields") == []
    assert "before" not in diff
    assert "after" not in diff
    # Associativity-erasure проверяем явно: ни сериализованный sha256,
    # ни consent_id не должны нигде встречаться в diff.
    serialized = repr(diff)
    assert record_sha256 not in serialized
    assert str(consent_id) not in serialized

    # Consent тоже получил audit на revoke — INSERT + UPDATE.
    consent_audit = await _audit_rows_for(
        db_session, entity_type="dna_consents", entity_id=consent_id
    )
    assert [r.action for r in consent_audit] == [
        AuditAction.INSERT.value,
        AuditAction.UPDATE.value,
    ]


@pytest.mark.db
@pytest.mark.integration
async def test_audit_log_idempotent_revoke_does_not_duplicate(
    app_client, seeded_user_and_tree, db_session
) -> None:
    """Повторный revoke (204 idempotent) не пишет вторую UPDATE-запись."""
    user_id, tree_id = seeded_user_and_tree

    create_resp = await app_client.post("/consents", json=_consent_payload(user_id, tree_id))
    consent_id = uuid.UUID(create_resp.json()["id"])

    first = await app_client.delete(f"/consents/{consent_id}")
    second = await app_client.delete(f"/consents/{consent_id}")
    assert first.status_code == 204
    assert second.status_code == 204

    rows = await _audit_rows_for(db_session, entity_type="dna_consents", entity_id=consent_id)
    actions = [r.action for r in rows]
    # INSERT + ровно один UPDATE — повторный delete не дублирует.
    assert actions == [AuditAction.INSERT.value, AuditAction.UPDATE.value]
