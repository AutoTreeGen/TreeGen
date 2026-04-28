"""arq job-функции для parser-service (Phase 3.5).

Содержит ``run_import_job`` — оркестратор обработки ``ImportJob`` с
публикацией прогресса в Redis pub/sub. SSE-эндпоинт api-gateway подписан
на канал ``job-events:{import_job_id}`` и стримит события в браузер
(``EventSource`` на фронте).

TODO: register in WorkerSettings.functions when worker PR lands.
   Параллельный PR (`feat/phase-3.5-arq-worker`) добавляет в этот файл
   ``WorkerSettings`` со списком ``functions``. Когда оба PR смерджатся —
   ``run_import_job`` нужно будет перечислить в ``functions``, чтобы arq
   зарегистрировал функцию у воркера.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from uuid import UUID

from shared_models.enums import ImportJobStatus
from shared_models.orm import ImportJob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from parser_service.database import get_engine
from parser_service.services.import_runner import run_import
from parser_service.services.progress import ProgressPublisher, Stage


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
