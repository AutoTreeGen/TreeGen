"""arq-воркер для parser-service (Phase 3.5).

Отдельный процесс, который слушает очередь ``imports`` в Redis и исполняет
зарегистрированные job-функции:

* :func:`noop_job` — placeholder/smoke job, эхо payload'а. Используется
  для проверки боевого пути enqueue → consume.
* :func:`run_import_job` — оркестратор обработки ``ImportJob`` с публикацией
  прогресса в Redis pub/sub. SSE-эндпоинт api подписан на канал
  ``job-events:{import_job_id}`` и стримит события в браузер
  (``EventSource`` на фронте).

Запуск локально::

    uv run arq parser_service.worker.WorkerSettings

arq смотрит атрибуты класса ``WorkerSettings`` (без инстанцирования) —
``redis_settings``, ``queue_name``, ``functions``, ``on_startup``,
``on_shutdown``. Это конвенция arq, не наша произвольная схема.

См. ROADMAP §7, ADR-0026.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from arq.connections import RedisSettings
from shared_models.enums import HypothesisComputeJobStatus, ImportJobStatus
from shared_models.orm import HypothesisComputeJob, ImportJob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from parser_service.database import get_engine
from parser_service.services.bulk_hypothesis_runner import (
    STAGE_FAILED,
    execute_compute_job,
)
from parser_service.services.import_runner import run_import
from parser_service.services.notifications import post_notify_request
from parser_service.services.progress import ProgressPublisher, Stage

logger = logging.getLogger(__name__)

# Дефолт совпадает с локальным docker-compose Redis (см. docker-compose.yml).
# В проде переопределяется через ENV ``REDIS_URL``.
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# Имя очереди фиксируем константой — оно одно и то же на стороне продьюсера
# (parser_service.queue.get_arq_pool) и консьюмера (этот воркер). Любая
# рассинхронизация = jobs молча уходят в /dev/null.
QUEUE_NAME = "imports"


def _redis_settings_from_env() -> RedisSettings:
    """Построить ``RedisSettings`` из переменной окружения ``REDIS_URL``.

    Используется и воркером (через ``WorkerSettings.redis_settings``), и
    клиентским кодом (через ``parser_service.queue.get_arq_pool``) — единый
    источник правды для адреса Redis.
    """
    url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    return RedisSettings.from_dsn(url)


async def noop_job(_ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Placeholder-job для smoke-тестов очереди.

    Изначально нужна была чтобы ``functions=[]`` не падал у arq на старте;
    осталась как самый дешёвый способ убедиться что enqueue → consume
    работает на боевой Redis-конфигурации (CI integration test).

    Args:
        _ctx: Контекст воркера (передаётся arq, содержит ``redis``, ``job_id``
            и др.). Не используется в noop, но обязан быть первым аргументом
            по конвенции arq.
        payload: Произвольные данные, которые отправил продьюсер.

    Returns:
        Эхо payload-а с маркером успеха.
    """
    logger.info("noop_job received payload: %s", payload)
    return {"status": "ok", "received": payload}


async def run_import_job(
    ctx: dict[str, Any],
    import_job_id: str,
    *,
    local_path: str,
    owner_email: str,
    tree_name: str | None = None,
) -> dict[str, Any]:
    """arq job: orchestrates ImportJob processing with progress publishing.

    Шаги:

    1. Найти существующий ``ImportJob`` row по ``import_job_id`` (sanity check).
    2. Запустить :func:`run_import` с :class:`ProgressPublisher`,
       подписанным на канал ``job-events:{import_job_id}``.
    3. На ошибке — записать описание в ``ImportJob.errors`` (jsonb-список) и
       перевести status в ``failed`` отдельной транзакцией, чтобы причина
       не потерялась при rollback основного импорта.

    ``local_path`` и ``owner_email`` приходят аргументами job'а из
    ``enqueue_job`` — на текущем этапе локальный путь до .ged. Phase 3.5.1
    заменит их на storage_uri (MinIO/GCS) + user_id с резолвом email.

    Args:
        ctx: arq-контекст. Ожидаем ключ ``redis`` (``ArqRedis``-клиент);
            если его нет — публикация прогресса деградирует в no-op.
        import_job_id: UUID существующего ImportJob row.
        local_path: Локальный путь до .ged-файла (см. TODO про storage_uri).
        owner_email: Email user'а-владельца дерева (создастся, если нет).
        tree_name: Имя нового дерева. По умолчанию — basename файла.

    Returns:
        Сводный dict с финальным статусом и stats — то, что arq сохранит
        в ``arq:result:<job_id>``.
    """
    redis_client = ctx.get("redis")
    channel = f"job-events:{import_job_id}"
    publisher = ProgressPublisher(redis_client, channel)

    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        result = await session.execute(select(ImportJob).where(ImportJob.id == UUID(import_job_id)))
        job = result.scalar_one_or_none()
        if job is None:
            msg = f"ImportJob {import_job_id} not found"
            raise LookupError(msg)

        try:
            await run_import(
                session,
                Path(local_path),
                owner_email=owner_email,
                tree_name=tree_name,
                source_filename=job.source_filename,
                progress=publisher,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            # Записываем причину в jsonb-список errors отдельной транзакцией —
            # rollback основного импорта не должен стирать диагностику.
            async with session_maker() as fail_session:
                fail_job = (
                    await fail_session.execute(
                        select(ImportJob).where(ImportJob.id == UUID(import_job_id))
                    )
                ).scalar_one_or_none()
                if fail_job is not None:
                    fail_job.status = ImportJobStatus.FAILED.value
                    fail_job.errors = [
                        *(fail_job.errors or []),
                        {
                            "kind": type(exc).__name__,
                            "message": str(exc),
                            "at": dt.datetime.now(dt.UTC).isoformat(),
                        },
                    ]
                    fail_job.finished_at = dt.datetime.now(dt.UTC)
                    await fail_session.commit()
            await publisher.publish(
                Stage.FINALIZING,
                current=0,
                total=1,
                message=f"failed: {exc}",
            )
            raise

        return {
            "import_job_id": import_job_id,
            "status": ImportJobStatus.SUCCEEDED.value,
            "stats": job.stats,
        }


async def run_bulk_hypothesis_job(
    ctx: dict[str, Any],
    compute_job_id: str,
) -> dict[str, Any]:
    """arq job: drain'ит ``HypothesisComputeJob`` через bulk_hypothesis_runner.

    Контракт зеркалит ``run_import_job``: один UUID-arg, тот же канал
    ``job-events:{compute_job_id}``, та же терминальная стадия в Stage-формате
    (см. ``bulk_hypothesis_runner.STAGE_*``). Это позволяет SSE-эндпоинту
    использовать единый pub/sub формат и единый close-on-terminal детектор.

    Сам ``execute_compute_job`` идемпотентен по статусу: если job уже не
    QUEUED — early-return без работы. Поэтому повторный enqueue (например,
    если воркер успел крашнуться после flush'а) не повторит loop.

    Args:
        ctx: arq-контекст. Ключ ``redis`` — ``ArqRedis``-клиент для
            публикации прогресса. Без него publisher деградирует в no-op.
        compute_job_id: UUID существующего HypothesisComputeJob row'а.

    Returns:
        Сводный dict с финальным статусом и progress — то, что arq
        сохранит в ``arq:result:<job_id>``.
    """
    redis_client = ctx.get("redis")
    channel = f"job-events:{compute_job_id}"
    publisher = ProgressPublisher(redis_client, channel)

    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    job_uuid = UUID(compute_job_id)
    async with session_maker() as session:
        result = await session.execute(
            select(HypothesisComputeJob).where(HypothesisComputeJob.id == job_uuid)
        )
        job = result.scalar_one_or_none()
        if job is None:
            msg = f"HypothesisComputeJob {compute_job_id} not found"
            raise LookupError(msg)

        try:
            final = await execute_compute_job(
                session,
                job_uuid,
                progress=publisher,
            )
        except Exception as exc:
            # ``execute_compute_job`` сам пишет FAILED-статус и публикует
            # терминальное событие в pub/sub. Здесь ловим только чтобы
            # arq записал result-row и не уронил воркер целиком —
            # пользователь увидит failure через SSE без exception leak'а.
            await publisher.publish(
                STAGE_FAILED,
                current=0,
                total=1,
                message=f"worker error: {exc}",
            )
            return {
                "compute_job_id": compute_job_id,
                "status": HypothesisComputeJobStatus.FAILED.value,
                "error": str(exc),
            }

        return {
            "compute_job_id": compute_job_id,
            "status": final.status,
            "progress": final.progress,
        }


async def dispatch_notification_job(
    _ctx: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """arq job: доставить notification-payload в notification-service.

    Phase 8.0 wire-up (ADR-0029). hypothesis_runner ставит этот job
    через :func:`parser_service.services.notifications.notify_hypothesis_pending_review`
    вместо синхронного httpx-POST'а из транзакции. Все ретраи и
    backoff — на стороне arq (см. ``WorkerSettings.functions``,
    арг ``max_tries`` пока дефолтный — 5).

    Args:
        _ctx: arq-контекст (``redis``, ``job_id``, ``job_try``...).
            Не нужен для HTTP-вызова, но обязан быть первым по
            конвенции arq.
        payload: Готовый body для ``POST /notify`` notification-service.
            Сформирован caller'ом — этот job ничего к нему не добавляет.

    Returns:
        Сводный dict с результатом доставки. ``delivered=True`` —
        notification-service вернул 2xx (создал или дедуплицировал).
        ``delivered=False`` — 4xx (плохой payload, retry бесполезен).

    Raises:
        httpx.HTTPError: 5xx или сеть. arq возьмёт на ретрай.
    """
    delivered = await post_notify_request(payload)
    return {
        "event_type": payload.get("event_type"),
        "user_id": payload.get("user_id"),
        "delivered": delivered,
    }


async def startup(ctx: dict[str, Any]) -> None:
    """Хук старта воркера — лог + место для будущей инициализации (DB pool и т.п.)."""
    logger.info("parser-service arq worker starting (queue=%s)", QUEUE_NAME)
    ctx["startup_logged"] = True


async def shutdown(_ctx: dict[str, Any]) -> None:
    """Хук остановки воркера — симметричный лог + cleanup в будущем."""
    logger.info("parser-service arq worker shutting down")


class WorkerSettings:
    """Конфигурация arq-воркера в формате, который ожидает arq CLI.

    arq читает атрибуты класса напрямую (не вызывает ``__init__``), поэтому
    всё объявлено как class-level. См. https://arq-docs.helpmanual.io/.
    """

    redis_settings: ClassVar[RedisSettings] = _redis_settings_from_env()
    queue_name: ClassVar[str] = QUEUE_NAME
    # arq принимает либо callable, либо результат ``arq.func(...)``. Голый
    # async-callable работает: arq оборачивает его сам с дефолтными настройками.
    functions: ClassVar[list[Any]] = [
        noop_job,
        run_import_job,
        run_bulk_hypothesis_job,
        dispatch_notification_job,
    ]
    on_startup = startup
    on_shutdown = shutdown
