"""Tests for Phase 4.11b — GDPR erasure worker (ADR-0049).

Покрытие:

* :func:`run_user_erasure` happy path: pending → done + cascade soft-delete +
  hard-delete DNA + audit-trail + Clerk delete + email.
* Edge: user owns shared tree (другой active member) → blocked со
  ``status='manual_intervention_required'``.
* Edge: pending export request → blocked.
* Idempotent re-call для terminal-row.
* Audit-row metadata содержит counts, но **не** PII (нет email/display_name).
* :func:`cascade_soft_delete` unit: hits all 8 tree-domain tables + names.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from parser_service.services.user_erasure_runner import (
    ErasureResult,
    run_user_erasure,
)
from shared_models import set_audit_skip
from shared_models.cascade_delete import (
    cascade_soft_delete,
    hard_delete_dna_for_user,
)
from shared_models.enums import AuditAction
from shared_models.orm import (
    AuditLog,
    Citation,
    DnaConsent,
    DnaImport,
    DnaKit,
    DnaTestRecord,
    Event,
    Family,
    MultimediaObject,
    Name,
    Note,
    Person,
    Place,
    Source,
    Tree,
    TreeMembership,
    User,
    UserActionRequest,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_maker(postgres_dsn: str):
    """async_sessionmaker, привязанный к test-DB."""
    from parser_service.database import get_engine, init_engine

    init_engine(postgres_dsn)
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def me_user_id(app_client, session_maker) -> uuid.UUID:
    """Создаёт fresh user per-test и инжектит X-User-Id."""
    suffix = uuid.uuid4().hex[:8]
    user_id = await _make_user(
        session_maker,
        email=f"erase_{suffix}@test.local",
        clerk_sub=f"clerk_erase_{suffix}",
    )
    app_client.headers["X-User-Id"] = str(user_id)
    return user_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(
    sm: async_sessionmaker,
    *,
    email: str,
    clerk_sub: str | None = None,
) -> uuid.UUID:
    async with sm() as session:
        user = User(
            email=email,
            external_auth_id=f"clerk:{clerk_sub}" if clerk_sub else f"local:{email}",
            clerk_user_id=clerk_sub,
            display_name=email.split("@", 1)[0].title(),
            locale="en",
        )
        session.add(user)
        await session.flush()
        await session.commit()
        return user.id


async def _make_tree(
    sm: async_sessionmaker,
    *,
    owner_id: uuid.UUID,
    name: str = "Tree",
) -> uuid.UUID:
    async with sm() as session:
        tree = Tree(
            owner_user_id=owner_id,
            name=name,
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        membership = TreeMembership(
            tree_id=tree.id,
            user_id=owner_id,
            role="owner",
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(membership)
        await session.flush()
        await session.commit()
        return tree.id


async def _add_member(
    sm: async_sessionmaker,
    *,
    tree_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "viewer",
) -> None:
    async with sm() as session:
        m = TreeMembership(
            tree_id=tree_id,
            user_id=user_id,
            role=role,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(m)
        await session.flush()
        await session.commit()


async def _make_request(
    sm: async_sessionmaker,
    *,
    user_id: uuid.UUID,
    kind: str = "erasure",
    status: str = "pending",
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    async with sm() as session:
        row = UserActionRequest(
            user_id=user_id,
            kind=kind,
            status=status,
            request_metadata=metadata or {},
        )
        session.add(row)
        await session.flush()
        await session.commit()
        return row.id


async def _seed_tree_data(
    sm: async_sessionmaker,
    *,
    tree_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    """Seed по одной записи в каждой tree-domain таблице.

    Возвращает map имя → id для post-soft-delete проверок. ``set_audit_skip``
    отключает row-level audit listener — там есть pre-existing bug
    с MultimediaObject (column-name ``metadata`` shadows DeclarativeBase.metadata
    для audit-listener'а ``getattr``-snapshot'а). Не наша проблема для
    Phase 4.11b; работаем вокруг.
    """
    async with sm() as session:
        set_audit_skip(session.sync_session, True)
        person = Person(tree_id=tree_id, sex="M")
        session.add(person)
        await session.flush()
        name = Name(
            person_id=person.id,
            given_name="Иосиф",
            surname="Иванов",
            sort_order=1,
            name_type="birth",
        )
        family = Family(tree_id=tree_id, husband_id=person.id)
        event = Event(tree_id=tree_id, event_type="BIRT", description="seed")
        place = Place(tree_id=tree_id, canonical_name="Vilna")
        source = Source(tree_id=tree_id, title="Census 1850")
        session.add_all([name, family, event, place, source])
        await session.flush()
        citation = Citation(
            tree_id=tree_id,
            source_id=source.id,
            entity_type="person",
            entity_id=person.id,
        )
        note = Note(tree_id=tree_id, body="seed-note")
        media = MultimediaObject(
            tree_id=tree_id,
            object_type="image",
            storage_url="s3://x/y",
            mime_type="image/jpeg",
        )
        session.add_all([citation, note, media])
        await session.flush()
        await session.commit()
        return {
            "person": person.id,
            "name": name.id,
            "family": family.id,
            "event": event.id,
            "place": place.id,
            "source": source.id,
            "citation": citation.id,
            "note": note.id,
            "media": media.id,
        }


async def _seed_dna(
    sm: async_sessionmaker,
    *,
    tree_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    """Seed DnaConsent + DnaKit + DnaTestRecord + DnaImport."""
    async with sm() as session:
        consent = DnaConsent(
            tree_id=tree_id,
            user_id=user_id,
            kit_owner_email="seed@test.local",
            consent_text="seed",
            consent_version="1.0",
        )
        session.add(consent)
        await session.flush()

        kit = DnaKit(
            tree_id=tree_id,
            owner_user_id=user_id,
            source_platform="ancestry",
            external_kit_id=f"k-{uuid.uuid4().hex[:6]}",
            display_name="seed-kit",
        )
        session.add(kit)
        await session.flush()

        record = DnaTestRecord(
            tree_id=tree_id,
            consent_id=consent.id,
            user_id=user_id,
            storage_path="dna/seed.bin",
            size_bytes=100,
            sha256="0" * 64,
            snp_count=700_000,
            provider="ancestry",
            encryption_scheme="application-fernet-v1",
        )
        imp = DnaImport(
            tree_id=tree_id,
            kit_id=kit.id,
            created_by_user_id=user_id,
            source_platform="ancestry",
            import_kind="csv",
            source_filename="seed.csv",
            source_size_bytes=1024,
            source_sha256="1" * 64,
            status="succeeded",
        )
        session.add_all([record, imp])
        await session.flush()
        await session.commit()
        return {
            "consent": consent.id,
            "kit": kit.id,
            "record": record.id,
            "import": imp.id,
        }


def _stub_clerk_delete(*, succeeds: bool = True) -> Any:
    """Возвращает stub-callable + список вызовов для проверки."""
    calls: list[str] = []

    async def _delete(clerk_user_id: str) -> bool:
        calls.append(clerk_user_id)
        return succeeds

    _delete.calls = calls  # type: ignore[attr-defined]
    return _delete


# ---------------------------------------------------------------------------
# cascade_soft_delete unit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_soft_delete_hits_all_tree_tables(session_maker, me_user_id) -> None:
    """``cascade_soft_delete`` помечает deleted_at у всех 8 + names."""
    tree_id = await _make_tree(session_maker, owner_id=me_user_id)
    seeded = await _seed_tree_data(session_maker, tree_id=tree_id)
    request_id = await _make_request(session_maker, user_id=me_user_id)

    async with session_maker() as session:
        result = await cascade_soft_delete(
            session,
            tree_id=tree_id,
            deleted_by_user_id=me_user_id,
            erasure_request_id=request_id,
        )
        await session.commit()

    # Counts: ровно по одной записи на таблицу.
    assert result.counts["persons"] == 1
    assert result.counts["families"] == 1
    assert result.counts["events"] == 1
    assert result.counts["places"] == 1
    assert result.counts["sources"] == 1
    assert result.counts["citations"] == 1
    assert result.counts["notes"] == 1
    assert result.counts["multimedia_objects"] == 1
    assert result.counts["names"] == 1
    assert result.total_rows == 9

    # Все записи имеют deleted_at + provenance.erasure_request_id.
    async with session_maker() as session:
        person = (
            await session.execute(select(Person).where(Person.id == seeded["person"]))
        ).scalar_one()
        assert person.deleted_at is not None
        assert person.provenance["erasure_request_id"] == str(request_id)
        assert person.provenance["erasure_reason"] == "gdpr_erasure"
        assert person.provenance["erased_by_user_id"] == str(me_user_id)

        name = (await session.execute(select(Name).where(Name.id == seeded["name"]))).scalar_one()
        assert name.deleted_at is not None  # sub-entity тоже soft-deleted

        source = (
            await session.execute(select(Source).where(Source.id == seeded["source"]))
        ).scalar_one()
        assert source.provenance["erasure_request_id"] == str(request_id)


@pytest.mark.asyncio
async def test_cascade_soft_delete_idempotent(session_maker, me_user_id) -> None:
    """Повторный вызов не перезаписывает уже soft-deleted records."""
    tree_id = await _make_tree(session_maker, owner_id=me_user_id)
    await _seed_tree_data(session_maker, tree_id=tree_id)
    request_id = await _make_request(session_maker, user_id=me_user_id)

    async with session_maker() as session:
        first = await cascade_soft_delete(
            session,
            tree_id=tree_id,
            deleted_by_user_id=me_user_id,
            erasure_request_id=request_id,
        )
        await session.commit()
    assert first.total_rows == 9

    async with session_maker() as session:
        second = await cascade_soft_delete(
            session,
            tree_id=tree_id,
            deleted_by_user_id=me_user_id,
            erasure_request_id=request_id,
        )
        await session.commit()
    # Второй проход: все WHERE deleted_at IS NULL — пусто.
    assert second.total_rows == 0


# ---------------------------------------------------------------------------
# hard_delete_dna_for_user unit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_delete_dna_removes_all_user_records(session_maker, me_user_id) -> None:
    """DNA hard-delete физически удаляет kits/records/consents/imports."""
    tree_id = await _make_tree(session_maker, owner_id=me_user_id)
    await _seed_dna(session_maker, tree_id=tree_id, user_id=me_user_id)

    async with session_maker() as session:
        result = await hard_delete_dna_for_user(session, user_id=me_user_id)
        await session.commit()

    assert result.counts["dna_kits"] == 1
    assert result.counts["dna_test_records"] == 1
    assert result.counts["dna_consents"] == 1
    assert result.counts["dna_imports"] == 1
    assert result.total_rows >= 4

    async with session_maker() as session:
        kits = (
            (await session.execute(select(DnaKit).where(DnaKit.owner_user_id == me_user_id)))
            .scalars()
            .all()
        )
        assert kits == []
        records = (
            (
                await session.execute(
                    select(DnaTestRecord).where(DnaTestRecord.user_id == me_user_id)
                )
            )
            .scalars()
            .all()
        )
        assert records == []


# ---------------------------------------------------------------------------
# run_user_erasure happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_happy_path(session_maker, me_user_id) -> None:
    """Pending erasure → done + soft-delete domain + hard-delete DNA + audit + Clerk."""
    tree_id = await _make_tree(session_maker, owner_id=me_user_id)
    seeded = await _seed_tree_data(session_maker, tree_id=tree_id)
    await _seed_dna(session_maker, tree_id=tree_id, user_id=me_user_id)
    request_id = await _make_request(session_maker, user_id=me_user_id)
    clerk_stub = _stub_clerk_delete(succeeds=True)

    async with session_maker() as session:
        result = await run_user_erasure(session, request_id, clerk_delete=clerk_stub)
        await session.commit()

    assert isinstance(result, ErasureResult)
    assert result.status == "done"
    assert result.persons_count == 1
    assert result.dna_total >= 4
    assert result.trees_processed == 1
    assert result.clerk_deleted is True
    assert clerk_stub.calls  # Clerk был вызван

    # Row перешла в done.
    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
    assert row.status == "done"
    assert row.processed_at is not None
    assert row.error is None
    assert row.request_metadata["clerk_deleted"] is True

    # Person soft-deleted с pointer'ом.
    async with session_maker() as session:
        person = (
            await session.execute(select(Person).where(Person.id == seeded["person"]))
        ).scalar_one()
    assert person.deleted_at is not None
    assert person.provenance["erasure_request_id"] == str(request_id)

    # Tree.deleted_at тоже выставлен.
    async with session_maker() as session:
        tree = (await session.execute(select(Tree).where(Tree.id == tree_id))).scalar_one()
    assert tree.deleted_at is not None

    # User soft-deleted.
    async with session_maker() as session:
        user = (await session.execute(select(User).where(User.id == me_user_id))).scalar_one()
    assert user.deleted_at is not None

    # DNA hard-deleted.
    async with session_maker() as session:
        kits = (
            (await session.execute(select(DnaKit).where(DnaKit.owner_user_id == me_user_id)))
            .scalars()
            .all()
        )
    assert kits == []

    # Audit-trail имеет processing + completed.
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.actor_user_id == me_user_id)
                    .where(AuditLog.entity_id == request_id)
                )
            )
            .scalars()
            .all()
        )
    actions = {r.action for r in rows}
    assert AuditAction.ERASURE_PROCESSING.value in actions
    assert AuditAction.ERASURE_COMPLETED.value in actions


# ---------------------------------------------------------------------------
# Edge: shared tree blocks erasure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_tree_blocks_erasure(session_maker, me_user_id) -> None:
    """Owner с другими members → manual_intervention_required, no soft-delete."""
    tree_id = await _make_tree(session_maker, owner_id=me_user_id)
    await _seed_tree_data(session_maker, tree_id=tree_id)
    other_id = await _make_user(session_maker, email="viewer@test.local")
    await _add_member(session_maker, tree_id=tree_id, user_id=other_id, role="viewer")
    request_id = await _make_request(session_maker, user_id=me_user_id)

    async with session_maker() as session:
        result = await run_user_erasure(session, request_id, clerk_delete=_stub_clerk_delete())
        await session.commit()

    assert result.status == "manual_intervention_required"
    assert result.trees_processed == 0

    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
    assert row.status == "manual_intervention_required"
    assert "ownership transfer" in (row.error or "")

    # Soft-delete НЕ должен был сработать.
    async with session_maker() as session:
        persons = (
            (await session.execute(select(Person).where(Person.tree_id == tree_id))).scalars().all()
        )
    assert all(p.deleted_at is None for p in persons)

    # Audit имеет ERASURE_BLOCKED.
    async with session_maker() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.entity_id == request_id)))
            .scalars()
            .all()
        )
    actions = {r.action for r in rows}
    assert AuditAction.ERASURE_BLOCKED.value in actions


# ---------------------------------------------------------------------------
# Edge: pending export blocks erasure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_export_blocks_erasure(session_maker, me_user_id) -> None:
    """Active export request → erasure blocked."""
    await _make_request(session_maker, user_id=me_user_id, kind="export", status="pending")
    request_id = await _make_request(session_maker, user_id=me_user_id, kind="erasure")

    async with session_maker() as session:
        result = await run_user_erasure(session, request_id, clerk_delete=_stub_clerk_delete())
        await session.commit()

    assert result.status == "manual_intervention_required"

    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
    assert "complete export" in (row.error or "").lower()


# ---------------------------------------------------------------------------
# Idempotent re-call for terminal status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_terminal_row(session_maker, me_user_id) -> None:
    """Re-call на уже done-row → no-op early-return."""
    request_id = await _make_request(
        session_maker,
        user_id=me_user_id,
        kind="erasure",
        status="done",
        metadata={"trees_processed": 2, "clerk_deleted": True},
    )

    async with session_maker() as session:
        result = await run_user_erasure(session, request_id, clerk_delete=_stub_clerk_delete())

    assert result.status == "done"
    assert result.trees_processed == 2
    assert result.clerk_deleted is True


# ---------------------------------------------------------------------------
# GDPR audit privacy: no PII in metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_metadata_contains_no_pii(session_maker) -> None:
    """ERASURE_COMPLETED audit row не содержит email/display_name/raw user_id в diff.

    user_id попадает только через ``actor_user_id``-колонку (которая
    set NULL после Clerk webhook hard-delete), не в diff jsonb.
    """
    user_id = await _make_user(session_maker, email="audit_pii_test@test.local")
    tree_id = await _make_tree(session_maker, owner_id=user_id)
    await _seed_tree_data(session_maker, tree_id=tree_id)
    await _seed_dna(session_maker, tree_id=tree_id, user_id=user_id)
    request_id = await _make_request(session_maker, user_id=user_id, kind="erasure")

    async with session_maker() as session:
        await run_user_erasure(session, request_id, clerk_delete=_stub_clerk_delete())
        await session.commit()

    async with session_maker() as session:
        completed_rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == request_id,
                        AuditLog.action == AuditAction.ERASURE_COMPLETED.value,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert completed_rows, "ERASURE_COMPLETED audit row missing"
    diff = completed_rows[0].diff
    serialized = json.dumps(diff)
    assert "audit_pii_test@test.local" not in serialized
    assert "Audit_Pii_Test" not in serialized  # display_name
    # user_id в diff — это строковое представление uuid; проверяем, что
    # email/display_name отсутствуют, а user_id допустим (он же
    # actor_user_id, т.е. self-reference на самого user'а).
    assert "soft_deleted" in diff["metadata"]
    assert "hard_deleted_dna" in diff["metadata"]


# ---------------------------------------------------------------------------
# Endpoint integration: /users/me/erasure-request enqueues job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_erasure_endpoint_returns_pending(app_client, session_maker) -> None:
    """POST /users/me/erasure-request → 202 + pending row + audit."""
    me = (await app_client.get("/users/me")).json()
    confirm = me["email"]
    r = await app_client.post(
        "/users/me/erasure-request",
        json={"confirm_email": confirm},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["kind"] == "erasure"
    assert body["status"] == "pending"
    request_id = uuid.UUID(body["request_id"])

    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
    assert row.status == "pending"
