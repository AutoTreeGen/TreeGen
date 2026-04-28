"""Абстракция очереди задач: arq (локально) или Cloud Tasks (прод).

Бэкенд выбирается через ENV ``PARSER_SERVICE_QUEUE_BACKEND``:

* ``arq`` (default) — пишет в локальный Redis через ``arq``.
* ``cloud_tasks`` — публикует HTTP-target таск в Cloud Tasks; целевой URL
  собирается из ``CLOUD_TASKS_WORKER_BASE_URL`` + ``/internal/jobs/{job_name}``.

Парный модуль к ``parser_service.worker``. arq-воркер живёт только локально;
в проде Cloud Tasks доставляет HTTP-запрос на сам parser-service, который
исполняет ту же job-функцию синхронно в request-хэндлере (см. ADR-0031).

Использование (callsite-агностичный API)::

    from parser_service.queue import enqueue_job

    await enqueue_job(
        "run_import_job",
        str(job.id), str(tmp_path),
        queue_name="imports",
        deduplication_key=f"import:{job.id}",
    )

Backward-compat: ``get_arq_pool()`` и ``close_arq_pool()`` оставлены для уже
существующих call-sites, использующих ``ArqRedis`` напрямую (через
``Depends``). Новый код должен использовать ``enqueue_job()``.

См. ADR-0031 «GCP deployment architecture».
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from arq import create_pool

from parser_service.worker import QUEUE_NAME, _redis_settings_from_env

if TYPE_CHECKING:
    from arq.connections import ArqRedis

logger = logging.getLogger(__name__)

# Поддерживаемые бэкенды. Любое другое значение → ValueError при первом enqueue.
BACKEND_ARQ = "arq"
BACKEND_CLOUD_TASKS = "cloud_tasks"
_VALID_BACKENDS = frozenset({BACKEND_ARQ, BACKEND_CLOUD_TASKS})


def _resolve_backend() -> str:
    """Прочитать ``PARSER_SERVICE_QUEUE_BACKEND`` (default = ``arq``)."""
    backend = os.environ.get("PARSER_SERVICE_QUEUE_BACKEND", BACKEND_ARQ).lower()
    if backend not in _VALID_BACKENDS:
        msg = (
            f"Unknown PARSER_SERVICE_QUEUE_BACKEND={backend!r}. "
            f"Expected one of: {sorted(_VALID_BACKENDS)}"
        )
        raise ValueError(msg)
    return backend


# ---- arq-бэкенд (локально) -----------------------------------------------

# Кэш пулов по event-loop'у. Один процесс может иметь несколько loop'ов
# (тесты, юпитер) — пул, созданный в одном loop, нельзя использовать в другом
# (asyncpg/redis-py привязывают Future к loop на момент создания). Поэтому
# индексируем по id(loop). Слабая привязка — loop переиспользуется до закрытия,
# id стабилен на его время жизни.
_pools: dict[int, ArqRedis] = {}
_lock = asyncio.Lock()


async def get_arq_pool() -> ArqRedis:
    """Вернуть singleton ``ArqRedis``-пул для текущего event-loop'а.

    Конфигурация Redis читается из ENV ``REDIS_URL`` (дефолт
    ``redis://localhost:6379/0``) — та же логика, что в воркере.

    Сохранён ради обратной совместимости со старыми call-site'ами,
    использующими ``Depends(get_arq_pool)`` в FastAPI. Новый код должен
    идти через :func:`enqueue_job`.

    Returns:
        Готовый ``ArqRedis``, на котором можно вызывать
        ``enqueue_job(name, *args, **kwargs)``.
    """
    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    cached = _pools.get(loop_id)
    if cached is not None:
        return cached

    async with _lock:
        # Двойная проверка: пока ждали lock, другой coroutine мог успеть создать пул.
        cached = _pools.get(loop_id)
        if cached is not None:
            return cached

        pool = await create_pool(
            _redis_settings_from_env(),
            default_queue_name=QUEUE_NAME,
        )
        _pools[loop_id] = pool
        return pool


async def close_arq_pool() -> None:
    """Закрыть все кэшированные пулы (для shutdown FastAPI / fixture-teardown)."""
    pools = list(_pools.values())
    _pools.clear()
    for pool in pools:
        await pool.close(close_connection_pool=True)


async def _enqueue_arq(
    job_name: str,
    *args: Any,
    queue_name: str | None,
    deduplication_key: str | None,
) -> None:
    """Поставить job в arq-очередь (через Redis)."""
    pool = await get_arq_pool()
    await pool.enqueue_job(
        job_name,
        *args,
        _queue_name=queue_name or QUEUE_NAME,
        _job_id=deduplication_key,
    )


# ---- Cloud Tasks-бэкенд (прод) -------------------------------------------


def _cloud_tasks_target_url(job_name: str) -> str:
    """Собрать HTTP target URL для Cloud Tasks worker'а.

    parser-service сам обслуживает ``POST /internal/jobs/{job_name}``
    (см. ADR-0031 §code). Базовый URL — собственный Cloud Run URL,
    приходит в ENV ``CLOUD_TASKS_WORKER_BASE_URL`` после деплоя.
    """
    base = os.environ.get("CLOUD_TASKS_WORKER_BASE_URL", "").rstrip("/")
    if not base:
        msg = (
            "CLOUD_TASKS_WORKER_BASE_URL is required when PARSER_SERVICE_QUEUE_BACKEND=cloud_tasks"
        )
        raise RuntimeError(msg)
    return f"{base}/internal/jobs/{job_name}"


def _cloud_tasks_queue_path(queue_name: str | None) -> str:
    """Собрать fully-qualified path Cloud Tasks-очереди.

    Имена очередей в проде содержат env-prefix (см. terraform queue module:
    ``staging-imports`` и т.п.), а callsite оперирует короткими именами
    (``imports``, ``hypotheses``). Маппинг идёт через
    ``CLOUD_TASKS_QUEUE_<NAME>`` env-переменные, выставляемые Terraform.

    Если переменная пустая — fallback на полное имя из ``CLOUD_TASKS_QUEUE_DEFAULT``.
    """
    short = (queue_name or QUEUE_NAME).upper().replace("-", "_")
    full = os.environ.get(f"CLOUD_TASKS_QUEUE_{short}")
    if full:
        return full
    fallback = os.environ.get("CLOUD_TASKS_QUEUE_DEFAULT")
    if fallback:
        return fallback
    msg = (
        f"Cloud Tasks queue path not configured: set "
        f"CLOUD_TASKS_QUEUE_{short} or CLOUD_TASKS_QUEUE_DEFAULT"
    )
    raise RuntimeError(msg)


async def _enqueue_cloud_tasks(
    job_name: str,
    *args: Any,
    queue_name: str | None,
    deduplication_key: str | None,
) -> None:
    """Опубликовать job в Cloud Tasks (HTTP target).

    Импорт ``google.cloud.tasks_v2`` ленивый — пакет ставится только в
    прод-образ (через optional-extra ``cloud-tasks``), локально мы его не
    тянем чтобы не утяжелять dev-окружение.
    """
    try:
        from google.cloud import tasks_v2  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "google-cloud-tasks not installed. Install with extras: "
            "`uv sync --extra cloud-tasks`. See ADR-0031."
        )
        raise RuntimeError(msg) from exc

    queue_path = _cloud_tasks_queue_path(queue_name)
    url = _cloud_tasks_target_url(job_name)

    payload = {"args": list(args)}
    body = json.dumps(payload).encode("utf-8")

    sa_email = os.environ.get("CLOUD_TASKS_INVOKER_SA_EMAIL")
    if not sa_email:
        msg = (
            "CLOUD_TASKS_INVOKER_SA_EMAIL is required (Cloud Run invoker SA "
            "for OIDC auth on the worker endpoint)."
        )
        raise RuntimeError(msg)

    task: dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "body": body,
            "oidc_token": {
                "service_account_email": sa_email,
                "audience": url,
            },
        },
    }
    if deduplication_key is not None:
        # Cloud Tasks принимает имя как fully-qualified path —
        # ``{queue}/tasks/{name}``. Имя должно быть alphanumeric+_-.
        task["name"] = f"{queue_path}/tasks/{deduplication_key.replace(':', '_').replace('/', '_')}"

    # Клиент создаём на каждый enqueue — gRPC connection-pool синглтон
    # внутри клиента, оверхед минимальный, зато нет state'а между loop'ами.
    client = tasks_v2.CloudTasksAsyncClient()
    try:
        await client.create_task(parent=queue_path, task=task)
    except Exception:  # pragma: no cover — оставляем стек на CI
        logger.exception(
            "cloud_tasks enqueue failed: job=%s queue=%s",
            job_name,
            queue_path,
        )
        raise


# ---- Публичный API --------------------------------------------------------


async def enqueue_job(
    job_name: str,
    *args: Any,
    queue_name: str | None = None,
    deduplication_key: str | None = None,
) -> None:
    """Поставить job в очередь — backend-agnostic.

    Args:
        job_name: Имя зарегистрированной job-функции (для arq) или sub-path
            HTTP worker-эндпоинта (для Cloud Tasks: ``POST /internal/jobs/{job_name}``).
        *args: Позиционные аргументы. Должны быть JSON-сериализуемыми.
        queue_name: Короткое имя очереди (``imports``, ``hypotheses``, ...).
            ``None`` → ``imports`` по умолчанию.
        deduplication_key: Идентификатор для дедупа постановок (одинаковый
            ключ → один и тот же job, повторная постановка no-op).

    Raises:
        ValueError: Неизвестный backend в env.
        RuntimeError: Cloud Tasks выбран, но конфигурация неполна.
    """
    backend = _resolve_backend()
    if backend == BACKEND_ARQ:
        await _enqueue_arq(
            job_name,
            *args,
            queue_name=queue_name,
            deduplication_key=deduplication_key,
        )
        return
    if backend == BACKEND_CLOUD_TASKS:
        await _enqueue_cloud_tasks(
            job_name,
            *args,
            queue_name=queue_name,
            deduplication_key=deduplication_key,
        )
        return
    # _resolve_backend уже бы упал; этот путь — защита от рефакторинга.
    msg = f"Unhandled backend: {backend}"
    raise ValueError(msg)
