"""Тесты async-импорта (Phase 3.5).

Покрывают новый контракт ``POST /imports`` (202 + enqueue), полей
``progress`` / ``cancel_requested`` в response, ``PATCH /cancel``,
а также SSE-эндпоинт с pubsub'ом через fakeredis.

Не используют testcontainers-postgres — pool/Redis замокан, БД-сессия
тоже подменена через ``app.dependency_overrides``. Тесты быстрые
(unit-уровень) и не требуют ``integration`` маркера.
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
def _disable_inline_import_mode() -> Iterator[None]:
    """Локально снимаем ``PARSER_SERVICE_IMPORT_INLINE=1``, который выставлен
    session-scoped автохуком в ``conftest.py``.

    Эти тесты проверяют именно асинхронный контракт (202 + enqueue + SSE);
    в inline-режиме endpoint вызывает ``run_import`` синхронно и возвращает
    201 — поведение, которое здесь как раз нельзя, чтобы тесты были
    репрезентативны для прод-пути.
    """
    saved = os.environ.pop("PARSER_SERVICE_IMPORT_INLINE", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["PARSER_SERVICE_IMPORT_INLINE"] = saved


_MINIMAL_GED = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
0 TRLR
"""


# ---------------------------------------------------------------------------
# Stub session — in-memory, без Postgres / testcontainers.
# ---------------------------------------------------------------------------


class _FakeJob:
    """In-memory ImportJob + Tree-replacement, удобный для unit-тестов API.

    Имеет атрибуты, которые читает Pydantic ``ImportJobResponse`` через
    ``model_validate(from_attributes=True)``. Не наследует ORM
    ImportJob, чтобы не тащить настоящий sqlalchemy.
    """

    def __init__(
        self,
        *,
        tree_id: uuid.UUID | None = None,
        source_filename: str | None = None,
        source_size_bytes: int | None = None,
        status: str = "queued",
        progress: dict[str, Any] | None = None,
        cancel_requested: bool = False,
    ) -> None:
        self.id = uuid.uuid4()
        self.tree_id = tree_id or uuid.uuid4()
        self.source_filename = source_filename
        self.source_size_bytes = source_size_bytes
        self.source_sha256 = None
        self.status = status
        self.stats: dict[str, int] = {}
        self.errors: list[dict[str, Any]] = []
        self.validation_findings: list[dict[str, Any]] = []
        self.progress = progress
        self.cancel_requested = cancel_requested
        self.error: str | None = None
        self.started_at: dt.datetime | None = None
        self.finished_at: dt.datetime | None = None
        self.created_at = dt.datetime.now(dt.UTC)
        # Mirror SQLAlchemy InstrumentedAttribute mutation semantics —
        # plain attribute write is enough for our тестов.
        self.created_by_user_id: uuid.UUID | None = uuid.uuid4()


class _StubResult:
    """Минимальный аналог ``Result`` — отдаёт scalar_one_or_none()."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _StubSession:
    """Захватывает добавленные модели и эмулирует select(ImportJob)/Tree.

    Поведение:
    * ``add(model)`` — складывает в общий store + проставляет id, если
      ORM присвоил бы его на flush'е.
    * ``execute(stmt)`` — для select'а ImportJob возвращает уже
      сохранённый job (ищем по where-clause's id).
    * ``flush() / commit() / rollback()`` — no-op.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        # Канал доступа из тестов — последний добавленный ImportJob.
        self.last_import_job: _FakeJob | None = None
        # Заранее подсаженные jobs (для GET / PATCH — они идут перед
        # созданием новых через POST).
        self._preloaded: dict[uuid.UUID, _FakeJob] = {}

    def preload(self, job: _FakeJob) -> None:
        self._preloaded[job.id] = job

    def add(self, model: Any) -> None:
        self.added.append(model)
        # Подражаем session.flush() — присваиваем id + созданный_at,
        # чтобы вызывающий код мог использовать `model.id` сразу.
        if not hasattr(model, "id") or model.id is None:
            model.id = uuid.uuid4()
        if hasattr(model, "__class__") and model.__class__.__name__ == "ImportJob":
            self.last_import_job = model

    async def execute(self, stmt: Any) -> _StubResult:
        # Naive проверка: `select(ImportJob).where(ImportJob.id == job_id)` —
        # достаём id из binary-clause. SQLAlchemy expression API стабильно
        # отдаёт литералы через ``stmt.whereclause.right.value``.
        try:
            value = stmt.whereclause.right.value
        except AttributeError:
            return _StubResult(None)
        return _StubResult(self._preloaded.get(value))

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest_asyncio.fixture
async def fake_session() -> AsyncIterator[_StubSession]:
    """Свежий ``_StubSession`` на каждый тест."""
    yield _StubSession()


@pytest_asyncio.fixture
async def fake_pool() -> AsyncIterator[AsyncMock]:
    """Mock arq pool с awaitable enqueue_job."""
    pool = MagicMock()
    pool.enqueue_job = AsyncMock(return_value=MagicMock(job_id="fake-job-id"))
    yield pool


@pytest_asyncio.fixture
async def app_client_async(
    fake_session: _StubSession,
    fake_pool: AsyncMock,
) -> AsyncIterator[tuple[AsyncClient, _StubSession, AsyncMock]]:
    """``AsyncClient`` с подменёнными session и arq-pool через DI overrides.

    Не поднимает Postgres — pure-in-memory unit-уровень. Возвращает
    тройку (client, session, pool) для тестов, которые проверяют
    side-effects на стабах.
    """
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
# Тесты POST/GET/PATCH.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_imports_returns_202_and_enqueues_job(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """POST /imports → 202 + enqueue("run_import_job", str(job.id), tmp_path).

    Проверяем, что:
    * статус 202 (Accepted), не 201;
    * в теле есть id, status=queued, events_url=/imports/{id}/events,
      progress=None, cancel_requested=False;
    * pool.enqueue_job вызван с правильным именем функции и UUID-строкой
      созданного job'а как первым позиционным аргументом.
    """
    client, _fake_session, fake_pool = app_client_async

    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    response = await client.post("/imports", files=files)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["progress"] is None
    assert body["cancel_requested"] is False
    job_id = body["id"]
    assert body["events_url"] == f"/imports/{job_id}/events"
    assert body["source_filename"] == "test.ged"

    fake_pool.enqueue_job.assert_awaited_once()
    args, kwargs = fake_pool.enqueue_job.await_args
    # Первый позиционный аргумент — имя arq-функции; второй — UUID job'а.
    assert args[0] == "run_import_job"
    assert args[1] == job_id  # str(job.id) совпадает с возвращённым id.
    # Третий — путь до tempfile (любая ненулевая строка с .ged суффиксом).
    assert args[2].endswith(".ged")
    assert kwargs.get("_queue_name") == "imports"


@pytest.mark.asyncio
async def test_get_imports_id_includes_progress_field(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """GET /imports/{id} включает ``progress`` (snapshot) и ``cancel_requested``.

    Подсаживаем job с уже опубликованным снапшотом (как будто worker
    однажды записал ProgressEvent в ``ImportJob.progress``) и ждём, что
    GET вернёт его 1:1.
    """
    client, fake_session, _ = app_client_async

    snapshot = {
        "stage": "persons",
        "current": 100,
        "total": 250,
        "message": "Loading persons",
        "ts": dt.datetime.now(dt.UTC).isoformat(),
    }
    job = _FakeJob(
        status="running",
        progress=snapshot,
        cancel_requested=False,
    )
    fake_session.preload(job)

    response = await client.get(f"/imports/{job.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["cancel_requested"] is False
    assert body["progress"] is not None
    assert body["progress"]["stage"] == "persons"
    assert body["progress"]["current"] == 100
    assert body["progress"]["total"] == 250
    assert body["progress"]["message"] == "Loading persons"


@pytest.mark.asyncio
async def test_patch_cancel_sets_db_flag(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """PATCH /imports/{id}/cancel → cancel_requested=True на ORM row'е."""
    client, fake_session, _ = app_client_async

    job = _FakeJob(status="running", cancel_requested=False)
    fake_session.preload(job)

    response = await client.patch(f"/imports/{job.id}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cancel_requested"] is True
    # PATCH также возвращает events_url — UI ещё не закрывает SSE,
    # ждёт финального события от worker'а.
    assert body["events_url"] == f"/imports/{job.id}/events"
    # Side-effect на ORM — флаг действительно вошёл в БД (in-memory).
    assert job.cancel_requested is True


@pytest.mark.asyncio
async def test_patch_cancel_terminal_job_is_noop(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """PATCH /cancel на уже succeeded job — 200 без изменений."""
    client, fake_session, _ = app_client_async

    job = _FakeJob(status="succeeded", cancel_requested=False)
    fake_session.preload(job)

    response = await client.patch(f"/imports/{job.id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["cancel_requested"] is False
    assert body["status"] == "succeeded"
    assert job.cancel_requested is False


@pytest.mark.asyncio
async def test_patch_cancel_unknown_job_returns_404(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """PATCH /cancel на неизвестный job → 404."""
    client, _, _ = app_client_async
    unknown = uuid.uuid4()
    response = await client.patch(f"/imports/{unknown}/cancel")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# SSE-тесты (fakeredis).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis_factory() -> AsyncIterator[Any]:
    """Singleton fakeredis instance + monkeypatch SSE module factory.

    Возвращает функцию ``publish(channel, data)``, которая публикует в
    тот же fakeredis, к которому подключится SSE-эндпоинт.
    """
    try:
        import fakeredis.aioredis as fakeredis_aioredis
    except ImportError:
        pytest.skip("fakeredis not installed")

    server = fakeredis_aioredis.FakeServer()

    def _factory() -> Any:
        return fakeredis_aioredis.FakeRedis(server=server, decode_responses=True)

    from parser_service.api import imports_sse

    saved = imports_sse._redis_client_factory
    imports_sse._redis_client_factory = _factory
    try:
        publisher = _factory()
        yield publisher
        with contextlib.suppress(Exception):
            await publisher.aclose()
    finally:
        imports_sse._redis_client_factory = saved


async def _read_sse_events(client: AsyncClient, url: str, *, max_events: int) -> list[str]:
    """Подключиться к SSE и прочитать до ``max_events`` data-payload'ов.

    Возвращает список JSON-строк из ``data:`` строк. Остальные строки
    (heartbeat-comments, event:..., id:...) игнорируем.
    """
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
async def test_sse_endpoint_yields_events_from_redis_pubsub(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
    fake_redis_factory: Any,
) -> None:
    """SSE форвардит сообщения из ``job-events:{id}``.

    Подсаживаем job, открываем SSE-стрим, публикуем 2 события через
    fakeredis, читаем их обратно. Терминальный stage гарантирует, что
    стрим закроется до timeout'а.
    """
    client, fake_session, _ = app_client_async

    job = _FakeJob(status="running")
    fake_session.preload(job)

    from parser_service.api.imports_sse import channel_name

    chan = channel_name(job.id)
    publisher = fake_redis_factory

    async def _publish_after_delay() -> None:
        # Маленькая задержка — даём подписчику успеть subscribe'нуться.
        await asyncio.sleep(0.05)
        await publisher.publish(
            chan,
            json.dumps(
                {
                    "stage": "persons",
                    "current": 50,
                    "total": 100,
                    "message": "halfway",
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
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
                    "message": "done",
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                }
            ),
        )

    publisher_task = asyncio.create_task(_publish_after_delay())
    try:
        events = await asyncio.wait_for(
            _read_sse_events(client, f"/imports/{job.id}/events", max_events=2),
            timeout=5.0,
        )
    finally:
        await publisher_task

    assert len(events) == 2
    first = json.loads(events[0])
    assert first["stage"] == "persons"
    assert first["current"] == 50
    second = json.loads(events[1])
    assert second["stage"] == "succeeded"


@pytest.mark.asyncio
async def test_sse_terminates_on_success_event(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
    fake_redis_factory: Any,
) -> None:
    """SSE-стрим закрывается как только worker опубликовал terminal stage.

    Публикуем сразу терминальное событие — клиент должен получить
    ровно 1 message и видеть EOF stream'а (не висеть на heartbeat).
    """
    client, fake_session, _ = app_client_async
    job = _FakeJob(status="running")
    fake_session.preload(job)

    from parser_service.api.imports_sse import channel_name

    chan = channel_name(job.id)
    publisher = fake_redis_factory

    async def _publish_terminal() -> None:
        await asyncio.sleep(0.05)
        await publisher.publish(
            chan,
            json.dumps(
                {
                    "stage": "succeeded",
                    "current": 0,
                    "total": 0,
                    "message": "instant terminal",
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                }
            ),
        )

    publisher_task = asyncio.create_task(_publish_terminal())
    try:
        # max_events=10 — но мы ожидаем, что стрим закроется после 1.
        events = await asyncio.wait_for(
            _read_sse_events(client, f"/imports/{job.id}/events", max_events=10),
            timeout=5.0,
        )
    finally:
        await publisher_task

    # Ровно один data-event дошёл; больше нет — стрим закрылся.
    assert len(events) == 1
    payload = json.loads(events[0])
    assert payload["stage"] == "succeeded"


@pytest.mark.asyncio
async def test_sse_endpoint_returns_404_for_unknown_job(
    app_client_async: tuple[AsyncClient, _StubSession, AsyncMock],
) -> None:
    """SSE на несуществующий job → 404 до открытия стрима."""
    client, _, _ = app_client_async
    unknown = uuid.uuid4()
    response = await client.get(f"/imports/{unknown}/events")
    assert response.status_code == 404
