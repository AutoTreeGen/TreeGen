"""Интеграционный тест: ``run_import`` стримит прогресс через ProgressPublisher.

Поднимаем testcontainers-postgres (через ``postgres_dsn``-фикстуру), создаём
``AsyncSession``, подписываемся на pub/sub-канал fakeredis и запускаем
``run_import`` с ``ProgressPublisher`` поверх этого fakeredis.

Проверяем, что в канале появляется минимум одно событие на каждую стадию
из ``Stage`` enum'а — это контракт, на который завязаны SSE-консьюмер
api-gateway и ``EventSource`` на фронте.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.db, pytest.mark.integration]


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
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
2 SOUR @S1@
3 PAGE p. 42
3 QUAY 3
1 OBJE @M1@
0 @I2@ INDI
1 NAME Mary /Smith/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1875
2 PLAC Vilna, Russian Empire
0 @S1@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
0 @M1@ OBJE
1 FILE photos/john_smith_1850.jpg
1 FORM jpg
1 TITL John Smith portrait, 1850
0 TRLR
"""


@pytest_asyncio.fixture
async def fake_redis() -> Any:
    """Async fakeredis-клиент с собственным in-memory сервером."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server)
    yield redis
    await redis.aclose()


@pytest.mark.asyncio
async def test_run_import_with_progress_publisher_emits_events(
    postgres_dsn: str,
    fake_redis: Any,
) -> None:
    """``run_import`` публикует хотя бы одно событие на каждую стадию.

    Запускаем подписчика на ``job-events:test`` параллельно с импортом,
    собираем все сообщения и проверяем, что встретились все стадии из
    ``Stage`` enum'а (parsing, entities, places, sources, events,
    multimedia, finalizing).
    """
    from parser_service.services.import_runner import run_import
    from parser_service.services.progress import ProgressPublisher, Stage
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    channel = "job-events:test"
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(channel)
    # Confirmation подписки — пропускаем.
    confirm = await pubsub.get_message(timeout=1.0)
    assert confirm is not None
    assert confirm["type"] == "subscribe"

    received: list[dict[str, Any]] = []

    async def collect() -> None:
        """Слушать pub/sub до cancellation, складывать payload'ы."""
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

    collector_task = asyncio.create_task(collect())

    publisher = ProgressPublisher(fake_redis, channel)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ged") as tmp:
        tmp.write(_MINIMAL_GED)
        ged_path = Path(tmp.name)

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        async with session_maker() as session:
            try:
                await run_import(
                    session,
                    ged_path,
                    owner_email="progress-test@example.com",
                    tree_name="progress-test",
                    source_filename="progress-test.ged",
                    progress=publisher,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        # Дать collector'у дочитать оставшиеся события до отмены.
        await asyncio.sleep(0.1)
    finally:
        collector_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector_task
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await engine.dispose()
        ged_path.unlink(missing_ok=True)

    # Должно быть минимум одно событие на каждую стадию из enum'а.
    stages_seen = {payload["stage"] for payload in received}
    expected_stages = {stage.value for stage in Stage}
    missing = expected_stages - stages_seen
    assert not missing, f"missing progress events for stages: {missing}; got={stages_seen}"

    # Sanity: payload'ы валидно сериализуются и содержат ключи stage/current/total.
    for payload in received:
        assert "stage" in payload
        assert isinstance(payload["current"], int)
        assert isinstance(payload["total"], int)


@pytest.mark.asyncio
async def test_run_import_without_progress_publisher_still_works(
    postgres_dsn: str,
) -> None:
    """``run_import`` с ``progress=None`` отрабатывает как раньше (regression).

    Защищаем backwards-compat: synchronous caller в API не должен заметить
    изменений после Phase 3.5.
    """
    from parser_service.services.import_runner import run_import
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ged") as tmp:
        tmp.write(_MINIMAL_GED)
        ged_path = Path(tmp.name)

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        async with session_maker() as session:
            try:
                job = await run_import(
                    session,
                    ged_path,
                    owner_email="no-progress@example.com",
                    tree_name="no-progress-tree",
                    source_filename="no-progress.ged",
                    progress=None,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        assert job.status == "succeeded"
        assert job.stats is not None
        assert job.stats["persons"] == 2
    finally:
        await engine.dispose()
        ged_path.unlink(missing_ok=True)
