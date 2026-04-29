"""Tests for Phase 4.11a — GDPR data export (ADR-0046).

Покрытие:

* HTTP `GET /users/me/requests` — cursor pagination, kind/status
  фильтры, isolation между пользователями, signed_url для completed
  exports.
* HTTP `POST /users/me/export-request` — enqueue + audit_log.
* `run_user_export` — happy path, idempotent re-call, failure path,
  ZIP-isolation (никаких чужих данных, никаких internal-only fields).
"""

from __future__ import annotations

import datetime as dt
import io
import json
import uuid
import zipfile
from typing import Any

import pytest
import pytest_asyncio
from parser_service.api.users import get_export_storage
from parser_service.config import Settings, get_settings
from parser_service.services.user_export_runner import run_user_export
from shared_models.enums import AuditAction
from shared_models.orm import (
    AuditLog,
    Tree,
    TreeMembership,
    User,
    UserActionRequest,
)
from shared_models.storage import InMemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _override_storage(app):
    """Per-test InMemoryStorage; injected как get_export_storage."""
    instance = InMemoryStorage(signing_key=b"test-signing-key-32bytes-pad-pad")
    app.dependency_overrides[get_export_storage] = lambda: instance
    # Stash на app для прямого доступа из теста.
    app.state.test_export_storage = instance
    yield instance
    app.dependency_overrides.pop(get_export_storage, None)


@pytest_asyncio.fixture(autouse=True)
async def _override_settings(app):
    """Override Settings для коротких TTL — детерминируем тесты."""
    overridden = Settings(
        export_url_ttl_seconds=900,
        export_object_ttl_days=30,
        export_max_zip_size_mb=10,  # маленький cap для теста size-guard
        clerk_issuer="https://test.clerk.local",
    )
    app.dependency_overrides[get_settings] = lambda: overridden
    app.state.test_export_settings = overridden
    yield overridden
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def storage(app) -> InMemoryStorage:
    """Альяс на _override_storage instance, для тестов с прямым access."""
    return app.state.test_export_storage  # type: ignore[no-any-return]


@pytest.fixture
def export_settings(app) -> Settings:
    """Альяс на _override_settings instance."""
    return app.state.test_export_settings  # type: ignore[no-any-return]


@pytest_asyncio.fixture
async def session_maker(postgres_dsn: str):
    """async_sessionmaker, привязанный к test-DB. Для прямых call'ов worker'а."""
    from parser_service.database import get_engine, init_engine

    init_engine(postgres_dsn)
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def me_user_id(app_client, session_maker) -> uuid.UUID:
    """Создаёт fresh user per-test и инжектит X-User-Id во все запросы.

    Изолирует тесты друг от друга в shared testcontainer-postgres'е:
    каждый тест видит только свои user_action_requests / audit_log rows
    (filter'ы в endpoint'ах по user_id). Autouse — все тесты получают
    свою песочницу автоматически.
    """
    suffix = uuid.uuid4().hex[:8]
    user_id = await _make_user(
        session_maker,
        email=f"u_{suffix}@test.local",
        clerk_sub=f"clerk_{suffix}",
    )
    app_client.headers["X-User-Id"] = str(user_id)
    return user_id


async def _make_user(
    sm: async_sessionmaker,
    *,
    email: str,
    clerk_sub: str | None = None,
) -> uuid.UUID:
    """Insert User-row, вернуть id."""
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


async def _make_request(
    sm: async_sessionmaker,
    *,
    user_id: uuid.UUID,
    kind: str = "export",
    status: str = "pending",
    created_at: dt.datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Insert UserActionRequest. created_at позволяет насеять history."""
    async with sm() as session:
        row = UserActionRequest(
            user_id=user_id,
            kind=kind,
            status=status,
            request_metadata=metadata or {},
        )
        if created_at is not None:
            row.created_at = created_at
            row.updated_at = created_at
        session.add(row)
        await session.flush()
        await session.commit()
        return row.id


async def _make_tree(
    sm: async_sessionmaker,
    *,
    owner_id: uuid.UUID,
    name: str,
) -> uuid.UUID:
    """Tree + OWNER membership. Возвращает tree_id."""
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


# ---------------------------------------------------------------------------
# GET /users/me/requests — pagination + filters + isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(app_client) -> None:
    """User без request'ов — пустой список + None cursor."""
    r = await app_client.get("/users/me/requests")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_pagination_cursor(
    app_client,
    session_maker,
) -> None:
    """Cursor walk через все request'ы, default limit=20."""
    # Find current user-id (auto-created by auth fixture).
    r = await app_client.get("/users/me")
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])

    # Seed 25 requests со sequential created_at.
    base = dt.datetime(2026, 4, 1, 12, 0, tzinfo=dt.UTC)
    for i in range(25):
        await _make_request(
            session_maker,
            user_id=user_id,
            kind="export",
            status="pending" if i % 2 == 0 else "done",
            created_at=base + dt.timedelta(seconds=i),
            metadata={"seq": i},
        )

    # Page 1 — default limit=20.
    r = await app_client.get("/users/me/requests")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 20
    assert body["next_cursor"] is not None
    # Newest first — seq=24 → 5.
    seqs = [item["request_metadata"]["seq"] for item in body["items"]]
    assert seqs == list(range(24, 4, -1))

    # Page 2 — should yield remaining 5 + next_cursor=None.
    r2 = await app_client.get(
        "/users/me/requests",
        params={"cursor": body["next_cursor"]},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["items"]) == 5
    assert body2["next_cursor"] is None
    seqs2 = [item["request_metadata"]["seq"] for item in body2["items"]]
    assert seqs2 == [4, 3, 2, 1, 0]


@pytest.mark.asyncio
async def test_list_filter_by_kind_and_status(app_client, session_maker) -> None:
    """kind и status фильтры независимо комбинируются."""
    r = await app_client.get("/users/me")
    user_id = uuid.UUID(r.json()["id"])

    await _make_request(session_maker, user_id=user_id, kind="export", status="pending")
    await _make_request(session_maker, user_id=user_id, kind="export", status="done")
    await _make_request(session_maker, user_id=user_id, kind="erasure", status="pending")

    r = await app_client.get("/users/me/requests", params={"kind": "export"})
    assert {item["kind"] for item in r.json()["items"]} == {"export"}

    r = await app_client.get("/users/me/requests", params={"status": "pending"})
    assert {item["status"] for item in r.json()["items"]} == {"pending"}

    r = await app_client.get("/users/me/requests", params={"kind": "export", "status": "done"})
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "export"
    assert items[0]["status"] == "done"


@pytest.mark.asyncio
async def test_list_isolation_between_users(app_client, session_maker) -> None:
    """Чужие request'ы не утекают даже при auth-сбое (WHERE user_id = $current)."""
    # Current user = X-User-Id absent → auto-created from clerk fixture.
    r_self = await app_client.get("/users/me")
    me_id = uuid.UUID(r_self.json()["id"])

    suffix = uuid.uuid4().hex[:8]
    other_id = await _make_user(
        session_maker,
        email=f"other_{suffix}@test.local",
        clerk_sub=f"other_sub_{suffix}",
    )
    await _make_request(session_maker, user_id=other_id, kind="export", metadata={"who": "other"})
    await _make_request(session_maker, user_id=me_id, kind="export", metadata={"who": "me"})

    r = await app_client.get("/users/me/requests")
    items = r.json()["items"]
    assert all(item["request_metadata"].get("who") != "other" for item in items)
    assert any(item["request_metadata"].get("who") == "me" for item in items)


@pytest.mark.asyncio
async def test_list_invalid_cursor_returns_422(app_client) -> None:
    """Битый cursor → 422 с понятным message'ем (не silent-ignore)."""
    r = await app_client.get("/users/me/requests", params={"cursor": "not-base64!!"})
    assert r.status_code == 422
    assert "Invalid cursor" in r.json()["detail"]


@pytest.mark.asyncio
async def test_list_signed_url_present_for_done_export(app_client, session_maker, storage) -> None:
    """Done export → signed_url + signed_url_expires_at в payload."""
    r = await app_client.get("/users/me")
    user_id = uuid.UUID(r.json()["id"])

    request_id = await _make_request(
        session_maker,
        user_id=user_id,
        kind="export",
        status="done",
        metadata={
            "bucket_key": f"gdpr-exports/{user_id}/{uuid.uuid4()}.zip",
            "size_bytes": 1234,
        },
    )
    # Кладём blob, чтобы InMemoryStorage его «увидел» (signed_download_url не
    # требует existence для S3, но для consistency теста — кладём).
    bucket_key = f"gdpr-exports/{user_id}/{request_id}.zip"
    await storage.put(bucket_key, b"fake zip content")
    # Подменим bucket_key в row на наш путь.
    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
        row.request_metadata = {**row.request_metadata, "bucket_key": bucket_key}
        await session.commit()

    r2 = await app_client.get("/users/me/requests")
    items = r2.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "done"
    assert item["signed_url"] is not None
    assert item["signed_url"].startswith("memory://")
    assert item["signed_url_expires_at"] is not None


@pytest.mark.asyncio
async def test_list_no_signed_url_for_pending(app_client, session_maker) -> None:
    """Pending/processing/failed — signed_url остаётся None."""
    r = await app_client.get("/users/me")
    user_id = uuid.UUID(r.json()["id"])

    await _make_request(session_maker, user_id=user_id, kind="export", status="pending")
    await _make_request(session_maker, user_id=user_id, kind="export", status="failed")

    r2 = await app_client.get("/users/me/requests")
    for item in r2.json()["items"]:
        assert item["signed_url"] is None
        assert item["signed_url_expires_at"] is None


# ---------------------------------------------------------------------------
# POST /users/me/export-request — enqueue + audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_request_enqueues_job(app_client, session_maker) -> None:
    """POST /export-request → 202 + audit EXPORT_REQUESTED + arq job enqueued."""
    from parser_service.queue import get_arq_pool

    fake_pool = app_client._transport.app.dependency_overrides[get_arq_pool]()

    r = await app_client.post("/users/me/export-request", json={})
    assert r.status_code == 202, r.text
    body = r.json()
    request_id = uuid.UUID(body["request_id"])
    assert body["kind"] == "export"
    assert body["status"] == "pending"

    # Audit-entry должна быть в audit_log.
    user_id = uuid.UUID((await app_client.get("/users/me")).json()["id"])
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.actor_user_id == user_id)
                    .where(AuditLog.entity_id == request_id)
                )
            )
            .scalars()
            .all()
        )
    actions = {r.action for r in rows}
    assert AuditAction.EXPORT_REQUESTED.value in actions

    # Job enqueued — fake pool накапливает вызовы.
    fake_pool.enqueue_job.assert_awaited()
    args, _kwargs = fake_pool.enqueue_job.call_args
    assert args[0] == "run_user_export_job"
    assert args[1] == str(request_id)


@pytest.mark.asyncio
async def test_erasure_request_writes_audit(app_client, session_maker) -> None:
    """POST /erasure-request → ERASURE_REQUESTED audit-entry."""
    me = (await app_client.get("/users/me")).json()
    confirm_email = me["email"]

    r = await app_client.post(
        "/users/me/erasure-request",
        json={"confirm_email": confirm_email},
    )
    assert r.status_code == 202, r.text
    request_id = uuid.UUID(r.json()["request_id"])

    user_id = uuid.UUID(me["id"])
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.actor_user_id == user_id)
                    .where(AuditLog.entity_id == request_id)
                )
            )
            .scalars()
            .all()
        )
    assert {r.action for r in rows} == {AuditAction.ERASURE_REQUESTED.value}


# ---------------------------------------------------------------------------
# Worker — happy path + idempotency + failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_happy_path(app_client, session_maker, storage, export_settings) -> None:
    """run_user_export: pending → done + ZIP в storage + audit-trail."""
    me = (await app_client.get("/users/me")).json()
    user_id = uuid.UUID(me["id"])
    tree_id = await _make_tree(session_maker, owner_id=user_id, name="My Family Tree")
    request_id = await _make_request(session_maker, user_id=user_id, kind="export")

    async with session_maker() as session:
        result = await run_user_export(
            session, request_id, storage=storage, settings=export_settings
        )
        await session.commit()

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
    assert row.request_metadata["bucket_key"] == result.bucket_key
    assert row.request_metadata["size_bytes"] == result.size_bytes

    # ZIP реально лежит в storage с правильным content.
    blob = await storage.get(result.bucket_key)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "profile.json" in names
        assert f"trees/{tree_id}.json" in names
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["user_id"] == str(user_id)
    assert manifest["request_id"] == str(request_id)
    assert manifest["manifest_version"] == "1.0"

    # Audit-trail имеет processing + completed (request audit может быть от endpoint'а).
    async with session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.actor_user_id == user_id)
                    .where(AuditLog.entity_id == request_id)
                )
            )
            .scalars()
            .all()
        )
    actions = {r.action for r in rows}
    assert AuditAction.EXPORT_PROCESSING.value in actions
    assert AuditAction.EXPORT_COMPLETED.value in actions


@pytest.mark.asyncio
async def test_worker_idempotent_on_done(
    app_client, session_maker, storage, export_settings
) -> None:
    """run_user_export повторно на done-row — early-return без re-upload'а."""
    me = (await app_client.get("/users/me")).json()
    user_id = uuid.UUID(me["id"])
    request_id = await _make_request(
        session_maker,
        user_id=user_id,
        kind="export",
        status="done",
        metadata={
            "bucket_key": f"gdpr-exports/{user_id}/{uuid.uuid4()}.zip",
            "size_bytes": 100,
        },
    )

    async with session_maker() as session:
        result = await run_user_export(
            session, request_id, storage=storage, settings=export_settings
        )
    assert result.bucket_key  # синтезирован из request_metadata
    # row не была изменена (не было ZIP-uploaded'а).
    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
    assert row.status == "done"


@pytest.mark.asyncio
async def test_worker_failure_records_failed_status(
    app_client, export_settings, session_maker
) -> None:
    """Storage failure → row в failed + EXPORT_FAILED audit + raise."""
    me = (await app_client.get("/users/me")).json()
    user_id = uuid.UUID(me["id"])
    request_id = await _make_request(session_maker, user_id=user_id, kind="export")

    class _BrokenStorage:
        async def put(self, *_a: object, **_kw: object) -> None:
            msg = "storage timeout"
            raise RuntimeError(msg)

        async def get(self, *_a: object, **_kw: object) -> bytes:
            msg = "never called"
            raise RuntimeError(msg)

        async def delete(self, *_a: object, **_kw: object) -> None:
            pass

        async def exists(self, *_a: object, **_kw: object) -> bool:
            return False

        async def signed_download_url(self, *_a: object, **_kw: object) -> object:
            msg = "never called"
            raise RuntimeError(msg)

    broken = _BrokenStorage()
    async with session_maker() as session:
        with pytest.raises(RuntimeError, match="storage timeout"):
            await run_user_export(
                session,
                request_id,
                storage=broken,
                settings=export_settings,  # type: ignore[arg-type]
            )
        await session.commit()  # persist failed-state

    async with session_maker() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.actor_user_id == user_id)
                    .where(AuditLog.entity_id == request_id)
                )
            )
            .scalars()
            .all()
        )
    assert row.status == "failed"
    assert row.error is not None
    assert "storage timeout" in row.error
    assert AuditAction.EXPORT_FAILED.value in {r.action for r in rows}


# ---------------------------------------------------------------------------
# GDPR isolation: ZIP не утекает чужие данные / internal-only fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zip_does_not_leak_other_users_data(
    app_client, session_maker, storage, export_settings
) -> None:
    """User A export не содержит ни tree-rows, ни DNA, ни audit-entries user'а B."""
    me = (await app_client.get("/users/me")).json()
    me_id = uuid.UUID(me["id"])
    suffix = uuid.uuid4().hex[:8]
    other_email = f"other_{suffix}@test.local"
    other_id = await _make_user(session_maker, email=other_email, clerk_sub=f"other_clerk_{suffix}")

    # У other есть своё дерево + audit-entry.
    other_tree_name = f"OTHER_FAMILY_TREE_{suffix}"
    other_tree = await _make_tree(session_maker, owner_id=other_id, name=other_tree_name)
    other_marker = f"OTHER_USER_DATA_{suffix}"
    async with session_maker() as session:
        session.add(
            AuditLog(
                tree_id=other_tree,
                entity_type="trees",
                entity_id=other_tree,
                action="insert",
                actor_user_id=other_id,
                actor_kind="user",
                diff={"who": other_marker},
            )
        )
        await session.commit()

    # У me есть своё дерево.
    await _make_tree(session_maker, owner_id=me_id, name="My Family Tree")
    request_id = await _make_request(session_maker, user_id=me_id, kind="export")

    async with session_maker() as session:
        result = await run_user_export(
            session, request_id, storage=storage, settings=export_settings
        )
        await session.commit()

    blob = await storage.get(result.bucket_key)
    full_zip = blob.decode("utf-8", errors="replace")
    # Hard checks — никаких маркеров чужого user'а в архиве.
    assert other_marker not in full_zip
    assert other_tree_name not in full_zip
    assert other_email not in full_zip


@pytest.mark.asyncio
async def test_zip_excludes_internal_fields(
    app_client, session_maker, storage, export_settings
) -> None:
    """Profile.json не содержит external_auth_id / clerk_user_id / fs_token_encrypted."""
    me = (await app_client.get("/users/me")).json()
    me_id = uuid.UUID(me["id"])
    request_id = await _make_request(session_maker, user_id=me_id, kind="export")

    async with session_maker() as session:
        result = await run_user_export(
            session, request_id, storage=storage, settings=export_settings
        )
        await session.commit()

    blob = await storage.get(result.bucket_key)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        profile = json.loads(zf.read("profile.json"))
        manifest = json.loads(zf.read("manifest.json"))

    forbidden = {"external_auth_id", "clerk_user_id", "fs_token_encrypted"}
    assert not (forbidden & set(profile.keys())), (
        f"Profile leaks internal fields: {forbidden & set(profile.keys())}"
    )
    # Manifest должен явно документировать exclusions.
    assert any("OAuth" in s or "auth identifier" in s for s in manifest["excluded"])
