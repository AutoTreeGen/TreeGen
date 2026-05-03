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
from shared_models.orm import HypothesisComputeJob, ImportJob, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from parser_service.config import get_settings
from parser_service.database import get_engine
from parser_service.fs_oauth import (
    TokenCryptoError,
    get_token_storage,
    is_fs_token_storage_configured,
)
from parser_service.jobs.erase_audio_session import erase_audio_session
from parser_service.jobs.transcribe_audio import transcribe_audio_session
from parser_service.jobs.voice_extract import voice_extract_job
from parser_service.services.auto_transfer import run_ownership_transfer
from parser_service.services.bulk_hypothesis_runner import (
    STAGE_FAILED,
    execute_compute_job,
)
from parser_service.services.familysearch_importer import import_fs_pedigree
from parser_service.services.import_runner import run_import
from parser_service.services.notifications import post_notify_request
from parser_service.services.progress import ProgressPublisher, Stage
from parser_service.services.user_erasure_runner import run_user_erasure
from parser_service.services.user_export_runner import run_user_export

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


async def run_fs_import_job(
    ctx: dict[str, Any],
    import_job_id: str,
    user_id: str,
    fs_person_id: str,
    generations: int,
) -> dict[str, Any]:
    """arq job: тянет FamilySearch pedigree и заливает в существующее дерево.

    Зеркалит :func:`run_import_job` по форме (job-row → publisher →
    success/fail-транзакция), но источник данных — FS API, не локальный
    .ged-файл. Токен берётся из ``users.fs_token_encrypted`` (см.
    ADR-0027) и **не** передаётся через arq-payload — Redis-стрим в
    общем случае может быть прочитан другими подписчиками.

    Args:
        ctx: arq-контекст. Ожидаем ключ ``redis`` (``ArqRedis``-клиент);
            если его нет — публикация прогресса деградирует в no-op.
        import_job_id: UUID существующего ``ImportJob`` row (status=queued).
        user_id: UUID пользователя, у которого хранится FS-токен.
        fs_person_id: focus-persona на FamilySearch (pedigree root).
        generations: глубина (1..8), уже валидирована HTTP-слоем.

    Returns:
        Сводный dict с финальным статусом и stats (см. ImportJob.stats).
    """
    redis_client = ctx.get("redis")
    channel = f"job-events:{import_job_id}"
    publisher = ProgressPublisher(redis_client, channel)

    settings = get_settings()
    if not is_fs_token_storage_configured(settings.fs_token_key):
        msg = "PARSER_SERVICE_FS_TOKEN_KEY is not configured"
        await publisher.publish(Stage.FINALIZING, current=0, total=1, message=msg)
        raise RuntimeError(msg)
    storage = get_token_storage(settings.fs_token_key)

    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    job_uuid = UUID(import_job_id)
    user_uuid = UUID(user_id)

    async with session_maker() as session:
        job = (
            await session.execute(select(ImportJob).where(ImportJob.id == job_uuid))
        ).scalar_one_or_none()
        if job is None:
            msg = f"ImportJob {import_job_id} not found"
            raise LookupError(msg)
        user = (
            await session.execute(select(User).where(User.id == user_uuid))
        ).scalar_one_or_none()
        if user is None:
            msg = f"User {user_id} not found"
            raise LookupError(msg)
        if not user.fs_token_encrypted:
            msg = f"User {user_id} has no FamilySearch token (disconnected mid-job?)"
            raise RuntimeError(msg)

        try:
            stored = storage.decrypt(user.fs_token_encrypted)
        except TokenCryptoError as e:
            msg = f"Cannot decrypt FS token for user {user_id}: {e}"
            raise RuntimeError(msg) from e

        # Перевод в RUNNING до начала тяжёлой работы — UI видит «крутилку».
        job.status = ImportJobStatus.RUNNING.value
        job.started_at = dt.datetime.now(dt.UTC)
        await session.flush()
        await publisher.publish(
            Stage.PARSING,
            current=0,
            total=generations,
            message=f"fetching pedigree for {fs_person_id} ({generations} generations)",
        )

        try:
            await import_fs_pedigree(
                session,
                access_token=stored.access_token,
                fs_person_id=fs_person_id,
                tree_id=job.tree_id,
                owner_user_id=user.id,
                generations=generations,
                existing_job_id=job.id,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            async with session_maker() as fail_session:
                fail_job = (
                    await fail_session.execute(select(ImportJob).where(ImportJob.id == job_uuid))
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

        await publisher.publish(
            Stage.FINALIZING,
            current=1,
            total=1,
            message="succeeded",
        )
        return {
            "import_job_id": import_job_id,
            "status": job.status,
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


async def run_user_export_job(
    _ctx: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """arq job: GDPR data-export для одного ``user_action_requests``-row.

    Phase 4.11a (ADR-0046). Job-функция тонкая: открывает session,
    конструирует storage backend по env-конвенциям, делегирует
    :func:`run_user_export`. На любом исключении — runner сам помечает
    row failed + audit перед re-raise; здесь мы только commit'им (для
    success) или rollback'им (для failure) внешнюю транзакцию.

    Auto-retry **отключён** на arq-уровне (max_tries=1 в WorkerSettings.
    functions). GDPR-export — heavy/expensive, повторы неэффективны;
    failure → user видит ``status='failed'`` + ``error`` в
    ``GET /users/me/requests`` и решает retry вручную через новый POST.

    Args:
        _ctx: arq-контекст; unused (storage берём из env).
        request_id: UUID UserActionRequest. Должен иметь kind='export'.

    Returns:
        Sterile dict для arq-result-row (логи / inspection).
    """
    from shared_models.storage import build_storage_from_env  # noqa: PLC0415  — defer heavy import

    storage = build_storage_from_env()
    settings = get_settings()

    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    request_uuid = UUID(request_id)
    async with session_maker() as session:
        try:
            result = await run_user_export(
                session,
                request_uuid,
                storage=storage,
                settings=settings,
            )
            await session.commit()
        except Exception:
            # run_user_export сам пишет failed-status + audit в session
            # перед re-raise — нам нужно это persist'ить отдельным commit'ом,
            # чтобы потеря не case'ила «зависание» в processing'е.
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Failed to persist export-failure state for %s", request_id)
            raise
        return {
            "request_id": str(result.request_id),
            "bucket_key": result.bucket_key,
            "size_bytes": result.size_bytes,
            "email_idempotency_key": result.email_idempotency_key,
        }


async def run_user_erasure_job(
    _ctx: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """arq job: GDPR right-of-erasure для одного ``user_action_requests``-row.

    Phase 4.11b (ADR-0049). Тонкая job-обёртка: открывает session,
    делегирует :func:`run_user_erasure`, commit'ит / rollback'ит.

    Auto-retry **отключён** на arq-уровне (max_tries=1 в WorkerSettings.functions).
    Erasure pipeline идемпотентен по terminal-статусам (early-return),
    но повторное выполнение processing-шагов может породить дубль-audit
    rows / лишние Clerk-вызовы. Failure → user видит ``status='failed'``
    в ``GET /users/me/requests`` + admin runs manual fix-up.

    Args:
        _ctx: arq-контекст; unused.
        request_id: UUID UserActionRequest. Должен иметь kind='erasure'.

    Returns:
        Sterile dict для arq-result-row (sumary, никакого PII).
    """
    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    request_uuid = UUID(request_id)
    async with session_maker() as session:
        try:
            result = await run_user_erasure(session, request_uuid)
            await session.commit()
        except Exception:
            # run_user_erasure сам пишет failed-status + audit перед re-raise;
            # отдельный commit для persist'а этого failure-state'а.
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Failed to persist erasure-failure state for %s", request_id)
            raise
        return {
            "request_id": str(result.request_id),
            "status": result.status,
            "trees_processed": result.trees_processed,
            "dna_total": result.dna_total,
            "clerk_deleted": result.clerk_deleted,
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


async def run_ownership_transfer_job(
    _ctx: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """arq job: process one ``UserActionRequest(kind='ownership_transfer')`` row.

    Phase 4.11c (см. ADR-0050). Auto-pick next-eligible editor +
    atomic swap + audit + email + (если blocked) notification к user'у.
    Без auto-retry — failure → user видит row в ``GET /users/me/requests``
    как ``status='failed'`` с error и решает manual transfer.

    Args:
        _ctx: arq-context — unused (storage/email-deps инициализируются
            sub-helper'ами по env).
        request_id: UUID UserActionRequest. Должен иметь kind='ownership_transfer'.

    Returns:
        Sterile dict для arq-result-row (request_id, blocked флаг).
    """
    request_uuid = UUID(request_id)
    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        try:
            result = await run_ownership_transfer(session, request_uuid)
            await session.commit()
        except Exception:
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to persist ownership-transfer state for %s",
                    request_id,
                )
            raise
        return {
            "request_id": str(result.request_id),
            "tree_id": str(result.tree_id),
            "new_owner_user_id": (
                str(result.new_owner_user_id) if result.new_owner_user_id is not None else None
            ),
            "blocked": result.blocked,
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
        run_fs_import_job,
        run_user_export_job,
        run_user_erasure_job,
        run_ownership_transfer_job,
        # Phase 10.9a — voice-to-tree.
        transcribe_audio_session,
        erase_audio_session,
        # Phase 10.9b — voice-to-tree NLU extraction (ADR-0075).
        voice_extract_job,
    ]
    on_startup = startup
    on_shutdown = shutdown
