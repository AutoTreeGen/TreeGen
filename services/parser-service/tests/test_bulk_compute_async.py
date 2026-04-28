"""Тесты async bulk-compute (Phase 7.5 finalize).

Покрывают новый контракт ``POST /trees/{tree_id}/hypotheses/compute-all``
(202 + arq enqueue) и SSE-эндпоинт компьют-job'а через fakeredis.

Сами loop-сценарии (cancel mid-flight, failed-job, idempotency) уже
живут в ``test_bulk_hypothesis_compute.py`` и работают через testcontainers-
postgres. Здесь — пара thin-стабов для проверки, что HTTP-слой ставит
job в очередь и SSE-эндпоинт форвардит pubsub-сообщения. Ни Postgres,
ни реальный Redis не поднимаем.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _disable_inline_bulk_compute_mode() -> Iterator[None]:
    """Снимаем session-wide ``PARSER_SERVICE_BULK_COMPUTE_INLINE=1``.

    Эти тесты проверяют именно async-контракт (202 + enqueue + SSE);
    в inline-режиме endpoint исполняет job синхронно и возвращает 201.
    """
    saved = os.environ.pop("PARSER_SERVICE_BULK_COMPUTE_INLINE", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["PARSER_SERVICE_BULK_COMPUTE_INLINE"] = saved


# ---------------------------------------------------------------------------
# In-memory stub session — без Postgres.
# Зеркалит test_imports_async._StubSession, но возвращает наш HypothesisComputeJob.
# ---------------------------------------------------------------------------


class _FakeComputeJob:
    """In-memory ``HypothesisComputeJob``-replacement.

    Имеет атрибуты, которые читает Pydantic ``HypothesisComputeJobResponse``
    через ``model_validate(from_attributes=True)``. Не наследует ORM-модель,
    чтобы не тащить настоящий sqlalchemy в unit-тест.
    """

    def __init__(
        self,
        *,
        tree_id: uuid.UUID | None = None,
        status: str = "queued",
        rule_ids: list[str] | None = None,
        cancel_requested: bool = False,
        progress: dict[str, Any] | None = None,
    ) -> None:
        self.id = uuid.uuid4()
        self.tree_id = tree_id or uuid.uuid4()
        self.status = status
        self.rule_ids = rule_ids
        self.cancel_requested = cancel_requested
        self.progress = progress or {
            "processed": 0,
            "total": 0,
            "hypotheses_created": 0,
        }
        self.error: str | None = None
        self.started_at: dt.datetime | None = None
        self.finished_at: dt.datetime | None = None
        self.created_at = dt.datetime.now(dt.UTC)
        self.created_by_user_id: uuid.UUID | None = None


class _StubResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _StubSession:
    """Минимальный async-session stub для bulk-compute API."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.preloaded: dict[uuid.UUID, _FakeComputeJob] = {}
        # Sentinel который POST-handler выставит на flush — тест может
        # подсадить job с предсказуемым id.
        self.next_job_id: uuid.UUID | None = None
        # Whether enqueue_compute_job's idempotency lookup should hit.
        self.idempotency_hit: _FakeComputeJob | None = None

    def preload(self, job: _FakeComputeJob) -> None:
        self.preloaded[job.id] = job

    def add(self, model: Any) -> None:
        self.added.append(model)
        if self.next_job_id is not None:
            model.id = self.next_job_id
        elif not getattr(model, "id", None):
            model.id = uuid.uuid4()
        # Эмулируем server-side default'ы, которые ставит SQLAlchemy на flush:
        # ``created_at`` приходит из CURRENT_TIMESTAMP, статус — из enum default.
        # Для unit-stub'а проще выставить руками — Postgres мы здесь не поднимаем.
        if not getattr(model, "created_at", None):
            model.created_at = dt.datetime.now(dt.UTC)

    async def execute(self, stmt: Any) -> _StubResult:
        # enqueue_compute_job делает select(...).where(...).order_by().limit(1).
        # Для простоты: если ``idempotency_hit`` подставлен — возвращаем его;
        # иначе - смотрим на простой select() по id (used by GET / SSE).
        if self.idempotency_hit is not None:
            return _StubResult(self.idempotency_hit)
        try:
            value = stmt.whereclause.right.value
        except AttributeError:
            return _StubResult(None)
        return _StubResult(self.preloaded.get(value))

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, _obj: Any, _attrs: list[str] | None = None) -> None:
        return None


@pytest_asyncio.fixture
async def fake_session() -> AsyncIterator[_StubSession]:
    yield _StubSession()


@pytest_asyncio.fixture
async def fake_pool() -> AsyncIterator[AsyncMock]:
    pool = MagicMock()
    pool.enqueue_job = AsyncMock(return_value=MagicMock(job_id="fake-bulk-id"))
    yield pool


@pytest_asyncio.fixture
async def app_client_async(
    fake_session: _StubSession,
    fake_pool: AsyncMock,
) -> AsyncIterator[tuple[AsyncClient, _StubSession, AsyncMock]]:
    from parser_service.database import get_session
    from parser_service.main import app
    from parser_service.queue import get_arq_pool

    async def _get_session_override() -> AsyncIterator[_StubSession]:
        yield fake_session

    async def _get_pool_override() -> AsyncMock:
        return fake_pool

    app.dependency_overrides[get_session] = _get_session_override
    app.dependency_overrides[get_arq_pool] = _get_pool_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, fake_session, fake_pool
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_arq_pool, None)


# ---------------------------------------------------------------------------
# POST /compute-all (async path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_compute_all_returns_202_and_enqueues(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """POST → 202 Accepted + enqueue_job('run_bulk_hypothesis_job', str(job.id))."""
    client, _session, pool = app_client_async
    tree_id = uuid.uuid4()

    response = await client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["tree_id"] == str(tree_id)
    assert body["progress"] == {"processed": 0, "total": 0, "hypotheses_created": 0}
    job_id = body["id"]
    assert body["events_url"] == f"/trees/{tree_id}/hypotheses/compute-jobs/{job_id}/events"

    pool.enqueue_job.assert_awaited_once()
    args, kwargs = pool.enqueue_job.await_args
    assert args[0] == "run_bulk_hypothesis_job"
    assert args[1] == job_id  # str(job.id)
    # ``_job_id`` префиксуется bulk-namespace'ом для arq-уровня dedup'а.
    assert kwargs.get("_job_id") == f"bulk-hypothesis:{job_id}"


# NB: idempotency-сценарий («повторный POST в течение часа возвращает
# тот же job_id, без второго enqueue») покрыт в test_bulk_hypothesis_compute.py
# через testcontainers-postgres. Воспроизвести его здесь без реального
# ORM сложно — ``enqueue_compute_job`` делает ``isinstance(existing,
# HypothesisComputeJob)`` после ``scalar_one_or_none()``; стаб-объект
# мимикрировать под ORM-класс не получится.


@pytest.mark.asyncio
async def test_post_compute_all_inline_mode_returns_201(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """Когда выставлен PARSER_SERVICE_BULK_COMPUTE_INLINE=1 — путь sync.

    Поскольку stub-session не поддерживает реальный execute_compute_job
    (нет dedup_finder, нет hypothesis_runner), мы патчим execute_compute_job
    на AsyncMock и проверяем что HTTP отдаёт 201, а не 202.
    """
    client, _session, pool = app_client_async
    tree_id = uuid.uuid4()

    os.environ["PARSER_SERVICE_BULK_COMPUTE_INLINE"] = "1"
    try:
        from parser_service.api import hypotheses

        original_execute = hypotheses.execute_compute_job

        async def _fake_execute(_session: Any, job_id: uuid.UUID, **_kw: Any) -> Any:
            stub = _FakeComputeJob(tree_id=tree_id, status="succeeded")
            stub.id = job_id
            stub.progress = {"processed": 5, "total": 5, "hypotheses_created": 3}
            return stub

        hypotheses.execute_compute_job = _fake_execute  # type: ignore[assignment]
        try:
            response = await client.post(
                f"/trees/{tree_id}/hypotheses/compute-all",
                json={},
            )
        finally:
            hypotheses.execute_compute_job = original_execute  # type: ignore[assignment]
    finally:
        os.environ.pop("PARSER_SERVICE_BULK_COMPUTE_INLINE", None)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    # Inline-режим не делает enqueue — sanity check.
    pool.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_patch_cancel_includes_events_url(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """PATCH /cancel → events_url в response (UI не закрывает SSE)."""
    client, session, _pool = app_client_async
    tree_id = uuid.uuid4()
    job = _FakeComputeJob(tree_id=tree_id, status="running", cancel_requested=False)
    session.preload(job)

    response = await client.patch(f"/hypotheses/compute-jobs/{job.id}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cancel_requested"] is True
    assert body["events_url"] == f"/trees/{tree_id}/hypotheses/compute-jobs/{job.id}/events"


# ---------------------------------------------------------------------------
# SSE (fakeredis pubsub)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis_factory() -> AsyncIterator[Any]:
    """Singleton fakeredis instance + monkeypatch hypotheses_sse factory."""
    try:
        import fakeredis.aioredis as fakeredis_aioredis
    except ImportError:
        pytest.skip("fakeredis not installed")

    server = fakeredis_aioredis.FakeServer()

    def _factory() -> Any:
        return fakeredis_aioredis.FakeRedis(server=server, decode_responses=True)

    from parser_service.api import hypotheses_sse

    saved = hypotheses_sse._redis_client_factory
    hypotheses_sse._redis_client_factory = _factory
    try:
        publisher = _factory()
        yield publisher
        with contextlib.suppress(Exception):
            await publisher.aclose()
    finally:
        hypotheses_sse._redis_client_factory = saved


async def _read_sse_events(client: AsyncClient, url: str, *, max_events: int) -> list[str]:
    events: list[str] = []
    async with client.stream("GET", url) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(line[len("data: ") :].strip())
                if len(events) >= max_events:
                    break
    return events


@pytest.mark.asyncio
async def test_sse_endpoint_yields_bulk_compute_stages(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
    fake_redis_factory: Any,
) -> None:
    """SSE форвардит publishing'и worker'а из pub/sub."""
    client, session, _pool = app_client_async

    tree_id = uuid.uuid4()
    job = _FakeComputeJob(tree_id=tree_id, status="running")
    session.preload(job)

    from parser_service.api.hypotheses_sse import channel_name

    chan = channel_name(job.id)
    publisher = fake_redis_factory

    async def _publish() -> None:
        await asyncio.sleep(0.05)
        await publisher.publish(
            chan,
            json.dumps(
                {
                    "stage": "iterating_pairs",
                    "current": 50,
                    "total": 100,
                    "message": "Iterating person pairs (50/100)",
                }
            ),
        )
        await asyncio.sleep(0.02)
        await publisher.publish(
            chan,
            json.dumps(
                {
                    "stage": "succeeded",
                    "current": 100,
                    "total": 100,
                    "message": "Created 7 hypotheses",
                }
            ),
        )

    pub_task = asyncio.create_task(_publish())
    try:
        events = await asyncio.wait_for(
            _read_sse_events(
                client,
                f"/trees/{tree_id}/hypotheses/compute-jobs/{job.id}/events",
                max_events=2,
            ),
            timeout=5.0,
        )
    finally:
        await pub_task

    assert len(events) == 2
    first = json.loads(events[0])
    assert first["stage"] == "iterating_pairs"
    assert first["current"] == 50
    second = json.loads(events[1])
    assert second["stage"] == "succeeded"


@pytest.mark.asyncio
async def test_sse_terminates_on_terminal_stage(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
    fake_redis_factory: Any,
) -> None:
    """Стрим закрывается после succeeded / failed / cancelled."""
    client, session, _pool = app_client_async
    tree_id = uuid.uuid4()
    job = _FakeComputeJob(tree_id=tree_id, status="running")
    session.preload(job)

    from parser_service.api.hypotheses_sse import channel_name

    chan = channel_name(job.id)
    publisher = fake_redis_factory

    async def _publish_terminal() -> None:
        await asyncio.sleep(0.05)
        await publisher.publish(
            chan,
            json.dumps(
                {
                    "stage": "cancelled",
                    "current": 3,
                    "total": 10,
                    "message": "Cancelled by user",
                }
            ),
        )

    pub_task = asyncio.create_task(_publish_terminal())
    try:
        events = await asyncio.wait_for(
            _read_sse_events(
                client,
                f"/trees/{tree_id}/hypotheses/compute-jobs/{job.id}/events",
                max_events=10,
            ),
            timeout=5.0,
        )
    finally:
        await pub_task

    assert len(events) == 1
    assert json.loads(events[0])["stage"] == "cancelled"


@pytest.mark.asyncio
async def test_sse_404_when_job_in_other_tree(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """job_id корректный, но из другого дерева → 404 (no info-leak)."""
    client, session, _pool = app_client_async
    tree_a = uuid.uuid4()
    tree_b = uuid.uuid4()
    job = _FakeComputeJob(tree_id=tree_a, status="running")
    session.preload(job)

    response = await client.get(f"/trees/{tree_b}/hypotheses/compute-jobs/{job.id}/events")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_sse_404_for_unknown_job(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """SSE для несуществующего job → 404 до открытия стрима."""
    client, _session, _pool = app_client_async
    response = await client.get(
        f"/trees/{uuid.uuid4()}/hypotheses/compute-jobs/{uuid.uuid4()}/events"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Worker-level test: run_bulk_hypothesis_job делегирует execute_compute_job.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_bulk_hypothesis_job_delegates_to_execute(monkeypatch) -> None:
    """worker.run_bulk_hypothesis_job вызывает execute_compute_job с job_id."""
    from parser_service import worker

    captured_call: dict[str, Any] = {}

    async def _fake_execute(_session: Any, job_id: uuid.UUID, **kw: Any) -> Any:
        captured_call["job_id"] = job_id
        captured_call["progress"] = kw.get("progress")
        result = MagicMock()
        result.status = "succeeded"
        result.progress = {"processed": 1, "total": 1, "hypotheses_created": 1}
        return result

    monkeypatch.setattr(worker, "execute_compute_job", _fake_execute)

    # Заглушки для get_engine / sessionmaker / select(...) lookup.
    fake_engine = MagicMock()
    monkeypatch.setattr(worker, "get_engine", lambda: fake_engine)

    job_uuid = uuid.uuid4()
    fake_job_row = MagicMock()
    fake_job_row.id = job_uuid

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def execute(self, _stmt: Any) -> Any:
            r = MagicMock()
            r.scalar_one_or_none = lambda: fake_job_row
            return r

    def _fake_sessionmaker(_engine: Any, **_kw: Any) -> Any:
        def _make() -> _FakeSession:
            return _FakeSession()

        return _make

    monkeypatch.setattr(worker, "async_sessionmaker", _fake_sessionmaker)

    redis_stub = AsyncMock()
    redis_stub.publish = AsyncMock()
    result = await worker.run_bulk_hypothesis_job(
        {"redis": redis_stub},
        str(job_uuid),
    )

    assert captured_call["job_id"] == job_uuid
    # Publisher подключён к каналу job-events:{id}.
    publisher = captured_call["progress"]
    assert publisher is not None
    assert publisher.channel == f"job-events:{job_uuid}"
    assert result == {
        "compute_job_id": str(job_uuid),
        "status": "succeeded",
        "progress": {"processed": 1, "total": 1, "hypotheses_created": 1},
    }
