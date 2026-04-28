"""Smoke-тесты arq-воркера parser-service (Phase 3.5).

Включает:

- юнит-тест без Redis: проверяет, что ``WorkerSettings`` корректно
  собрана и в ``functions`` лежит ``noop_job``;
- интеграционный smoke (``-m integration -m slow``): требует живой Redis,
  поднимает воркер subprocess'ом, ставит job через ``get_arq_pool`` и
  читает результат. В CI по умолчанию пропускается.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Юнит-тест — без Redis, без subprocess
# ---------------------------------------------------------------------------


def test_worker_settings_registers_noop_job() -> None:
    """``WorkerSettings.functions`` должен содержать ``noop_job``.

    arq при старте воркера читает атрибут класса напрямую — если в реестре
    окажется пусто или там окажется не та функция, пайплайн Phase 3.5
    сломается. Тест — дешёвая страховка против переименования/удаления.
    """
    from parser_service.worker import WorkerSettings, noop_job

    assert noop_job in WorkerSettings.functions
    assert WorkerSettings.queue_name == "imports"
    # ``redis_settings`` — экземпляр RedisSettings из arq, не None.
    assert WorkerSettings.redis_settings is not None


@pytest.mark.asyncio
async def test_noop_job_returns_payload_echo() -> None:
    """noop_job эхо-возвращает входной payload в обёртке status=ok."""
    from parser_service.worker import noop_job

    result = await noop_job({}, {"foo": "bar"})
    assert result == {"status": "ok", "received": {"foo": "bar"}}


# ---------------------------------------------------------------------------
# Интеграционный smoke — нужен живой Redis (docker compose up redis)
# ---------------------------------------------------------------------------


def _redis_available() -> bool:
    """Быстрая проверка доступности Redis по адресу из ENV."""
    try:
        import redis  # type: ignore[import-not-found]
    except ImportError:
        return False

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=1)
        client.ping()
        client.close()
    except Exception:
        # Любая проблема при коннекте/пинге трактуется как «Redis недоступен» —
        # тест должен быть skip-ed, а не упасть с непонятной ошибкой.
        return False
    return True


@pytest.fixture
def worker_subprocess() -> Iterator[subprocess.Popen[bytes]]:
    """Поднять arq-воркер дочерним процессом и убить после теста."""
    env = os.environ.copy()
    env.setdefault("REDIS_URL", "redis://localhost:6379/0")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "arq",
            "parser_service.worker.WorkerSettings",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Дать воркеру секунду на коннект к Redis перед enqueue.
    time.sleep(1.0)
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
async def test_worker_executes_noop_job_via_pool(
    worker_subprocess: subprocess.Popen[bytes],  # noqa: ARG001 — фикстура поднимает воркер на время теста
) -> None:
    """End-to-end: enqueue noop_job через get_arq_pool → воркер исполняет → результат равен echo."""
    if not _redis_available():
        pytest.skip("Redis недоступен по REDIS_URL — пропускаем integration smoke")

    from parser_service.queue import close_arq_pool, get_arq_pool

    try:
        pool = await get_arq_pool()
        job = await pool.enqueue_job("noop_job", {"hello": "phase-3.5"})
        assert job is not None, "enqueue_job вернул None — очередь не приняла job"

        # Воркер исполняет job почти моментально, но дадим до 10 секунд.
        deadline = time.monotonic() + 10.0
        result = None
        while time.monotonic() < deadline:
            try:
                result = await job.result(timeout=1.0)
                break
            except TimeoutError:
                continue

        assert result == {"status": "ok", "received": {"hello": "phase-3.5"}}
    finally:
        await close_arq_pool()
