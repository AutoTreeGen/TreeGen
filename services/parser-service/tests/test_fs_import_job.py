"""Тесты ``parser_service.worker.run_fs_import_job`` (Phase 5.1).

Проверяют end-to-end сценарий arq job'а: pre-existing ImportJob row
из POST-эндпоинта → worker берёт токен из БД, расшифровывает, тянет
pedigree (FS-клиент замокан), заливает в дерево через
``import_fs_pedigree(..., existing_job_id=...)``, обновляет stats и
status=succeeded.

Redis для ProgressPublisher — fakeredis с pubsub-перехватом, чтобы
убедиться, что worker эмитирует хотя бы одно событие.

Маркеры: ``db`` + ``integration`` (testcontainers Postgres) — миграция
0012 нужна для ``users.fs_token_encrypted``.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from familysearch_client import FsFact, FsName, FsPedigreeNode, FsPerson
from shared_models.enums import (
    ImportJobStatus,
    ImportSourceKind,
    TreeVisibility,
)
from shared_models.orm import ImportJob, Tree, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis() -> Any:
    """Async fakeredis-клиент с pubsub-сервером."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def fs_token_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Сгенерировать Fernet-ключ и подставить в ENV."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("PARSER_SERVICE_FS_TOKEN_KEY", key)
    from parser_service.config import get_settings

    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()  # type: ignore[attr-defined]
    return key


def _solo_pedigree() -> FsPedigreeNode:
    """Минимальный pedigree: один focus-person с Birth-фактом."""
    return FsPedigreeNode(
        person=FsPerson(
            id="KW7S-VQJ",
            names=(
                FsName(
                    full_text="Solo Person",
                    given="Solo",
                    surname="Person",
                    preferred=True,
                ),
            ),
            facts=(
                FsFact(
                    type="Birth",
                    date_original="1900",
                    place_original="Brooklyn, New York",
                ),
            ),
        )
    )


def _patch_fs_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pedigree: FsPedigreeNode,
) -> None:
    """Подменяет FamilySearchClient в importer на stub-фабрику."""

    class _StubFsClientCM:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        async def __aenter__(self) -> _StubFsClientCM:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get_pedigree(
            self,
            person_id: str,  # noqa: ARG002
            *,
            generations: int = 4,  # noqa: ARG002
        ) -> FsPedigreeNode:
            return pedigree

    monkeypatch.setattr(
        "parser_service.services.familysearch_importer.FamilySearchClient",
        _StubFsClientCM,
    )


@pytest_asyncio.fixture
async def seeded_user_tree_and_job(
    postgres_dsn: str,
    fs_token_key: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Создать User (с зашифрованным токеном) + Tree + queued ImportJob.

    Возвращает (user_id, tree_id, import_job_id) для use в job-вызове.
    """
    from parser_service.fs_oauth import FsStoredToken, get_token_storage

    storage = get_token_storage(fs_token_key)
    token = FsStoredToken(
        access_token="atk-fake",
        refresh_token="rtk-fake",
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        scope="openid",
        fs_user_id="MMMM-MMM",
        stored_at=dt.datetime.now(dt.UTC),
    )
    ciphertext = storage.encrypt(token)

    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = User(
                email=f"fs-job-{uuid.uuid4().hex[:8]}@example.com",
                external_auth_id=f"local:fs-job-{uuid.uuid4().hex[:8]}",
                display_name="FS Job Test",
                locale="en",
                fs_token_encrypted=ciphertext,
            )
            session.add(user)
            await session.flush()

            tree = Tree(
                owner_user_id=user.id,
                name=f"FS Job Tree {uuid.uuid4().hex[:6]}",
                visibility=TreeVisibility.PRIVATE.value,
                default_locale="en",
                settings={},
                provenance={},
                version_id=1,
            )
            session.add(tree)
            await session.flush()

            job = ImportJob(
                tree_id=tree.id,
                created_by_user_id=user.id,
                source_kind=ImportSourceKind.FAMILYSEARCH.value,
                status=ImportJobStatus.QUEUED.value,
                stats={},
                errors=[],
                progress=None,
                cancel_requested=False,
            )
            session.add(job)
            await session.commit()
            return user.id, tree.id, job.id
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_fs_import_job_succeeds_and_updates_existing_job(
    seeded_user_tree_and_job: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    postgres_dsn: str,
    fake_redis: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker берёт queued job, тянет pedigree, обновляет тот же row до succeeded."""
    user_id, tree_id, job_id = seeded_user_tree_and_job
    _patch_fs_client(monkeypatch, pedigree=_solo_pedigree())

    # init_engine, чтобы worker мог открыть session (он использует
    # parser_service.database.get_engine).
    import os

    os.environ["PARSER_SERVICE_DATABASE_URL"] = postgres_dsn
    from parser_service.database import dispose_engine, init_engine

    init_engine(postgres_dsn)

    from parser_service.worker import run_fs_import_job

    ctx = {"redis": fake_redis}
    result = await run_fs_import_job(
        ctx,
        str(job_id),
        str(user_id),
        "KW7S-VQJ",
        1,
    )

    assert result["import_job_id"] == str(job_id)
    assert result["status"] == ImportJobStatus.SUCCEEDED.value
    assert result["stats"]["persons"] == 1
    assert result["stats"]["events"] == 1

    # Проверяем, что в БД именно тот же row обновился (не создан новый).
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            res = await session.execute(select(ImportJob).where(ImportJob.tree_id == tree_id))
            jobs = list(res.scalars().all())
        assert len(jobs) == 1, "worker should reuse existing job, not create new"
        assert jobs[0].id == job_id
        assert jobs[0].status == ImportJobStatus.SUCCEEDED.value
        assert jobs[0].started_at is not None
        assert jobs[0].finished_at is not None
    finally:
        await engine.dispose()
        await dispose_engine()


@pytest.mark.asyncio
async def test_run_fs_import_job_publishes_progress_events(
    seeded_user_tree_and_job: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    postgres_dsn: str,
    fake_redis: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker эмитирует хотя бы одно событие на канал ``job-events:{id}``."""
    user_id, _tree_id, job_id = seeded_user_tree_and_job
    _patch_fs_client(monkeypatch, pedigree=_solo_pedigree())

    import os

    os.environ["PARSER_SERVICE_DATABASE_URL"] = postgres_dsn
    from parser_service.database import dispose_engine, init_engine

    init_engine(postgres_dsn)

    channel = f"job-events:{job_id}"
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(channel)
    confirm = await pubsub.get_message(timeout=1.0)
    assert confirm is not None
    assert confirm["type"] == "subscribe"

    received: list[dict[str, Any]] = []

    async def collect() -> None:
        try:
            while True:
                msg = await pubsub.get_message(timeout=2.0)
                if msg is None:
                    continue
                if msg["type"] != "message":
                    continue
                received.append(json.loads(msg["data"]))
        except asyncio.CancelledError:
            return

    listener = asyncio.create_task(collect())
    try:
        from parser_service.worker import run_fs_import_job

        await run_fs_import_job(
            {"redis": fake_redis},
            str(job_id),
            str(user_id),
            "KW7S-VQJ",
            1,
        )
        # Дать листенеру дочитать буфер.
        await asyncio.sleep(0.05)
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await dispose_engine()

    stages = [event["stage"] for event in received]
    assert "parsing" in stages, f"expected parsing event, got {stages}"
    assert "finalizing" in stages, f"expected finalizing event, got {stages}"


@pytest.mark.asyncio
async def test_run_fs_import_job_fails_when_user_disconnected(
    postgres_dsn: str,
    fs_token_key: str,  # noqa: ARG001 — side-effect: подставляет ENV-ключ
    fake_redis: Any,
) -> None:
    """User без fs_token_encrypted → RuntimeError, ImportJob → failed."""
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            email=f"fs-no-token-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:fs-no-token-{uuid.uuid4().hex[:8]}",
            display_name="No Token",
            locale="en",
            fs_token_encrypted=None,
        )
        session.add(user)
        await session.flush()
        tree = Tree(
            owner_user_id=user.id,
            name="t",
            visibility=TreeVisibility.PRIVATE.value,
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        job = ImportJob(
            tree_id=tree.id,
            created_by_user_id=user.id,
            source_kind=ImportSourceKind.FAMILYSEARCH.value,
            status=ImportJobStatus.QUEUED.value,
            stats={},
            errors=[],
            progress=None,
            cancel_requested=False,
        )
        session.add(job)
        await session.commit()
        user_id, job_id = user.id, job.id
    await engine.dispose()

    import os

    os.environ["PARSER_SERVICE_DATABASE_URL"] = postgres_dsn
    from parser_service.database import dispose_engine, init_engine

    init_engine(postgres_dsn)
    try:
        from parser_service.worker import run_fs_import_job

        with pytest.raises(RuntimeError, match="no FamilySearch token"):
            await run_fs_import_job(
                {"redis": fake_redis},
                str(job_id),
                str(user_id),
                "KW7S-VQJ",
                1,
            )
    finally:
        await dispose_engine()
