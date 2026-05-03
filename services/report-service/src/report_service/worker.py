"""arq-воркер для report-service (Phase 24.4 / ADR-0078).

Отдельный процесс, который слушает очередь ``report-bundles`` в Redis и
исполняет:

* :func:`generate_report_bundle_job` — main worker entry: получает
  ``job_id``, грузит row, генерирует per-pair PDF'ы через 24.3 функцию,
  собирает bundle (ZIP или consolidated PDF), загружает в storage.
* :func:`purge_expired_bundles_job` — periodic sweep: удаляет blobs +
  rows с ``ttl_expires_at < now()``.

Запуск локально::

    uv run arq report_service.worker.WorkerSettings

arq смотрит атрибуты класса ``WorkerSettings`` (без инстанцирования) —
``redis_settings``, ``queue_name``, ``functions``, ``cron_jobs``,
``on_startup``, ``on_shutdown``. Это конвенция arq, не наша произвольная
схема.

Mirror parser-service.worker pattern; same Redis URL via env (``REDIS_URL``).
Очередь ``report-bundles`` — отдельная от parser-service ``imports``,
чтобы PDF-render не делил конкурентность с GEDCOM import / inference.
См. ADR-0078 §"Worker placement".
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar
from uuid import UUID

from arq.connections import RedisSettings
from arq.cron import cron
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from report_service.config import get_settings
from report_service.database import get_engine, init_engine

logger = logging.getLogger(__name__)

DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# Имя очереди — отдельное от parser-service ``imports`` (см. модуль docstring).
QUEUE_NAME = "report-bundles"


def _redis_settings_from_env() -> RedisSettings:
    """``RedisSettings`` из ``REDIS_URL``. Единый источник правды для arq pool + worker."""
    url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    return RedisSettings.from_dsn(url)


async def generate_report_bundle_job(_ctx: dict[str, Any], job_id: str) -> dict[str, Any]:
    """arq entry: bulk-bundle generation для одного :class:`ReportBundleJob`.

    Args:
        _ctx: arq-контекст. Не используется — собственная сессия БД.
        job_id: UUID :class:`ReportBundleJob`-row'а как строка (JSON-friendly).

    Returns:
        ``{"status": "completed"|"failed"|"cancelled", "completed": N, "failed": M}``.

    Делегирует в :func:`report_service.bundles.runner.run_bundle_job`,
    чтобы worker entry-point не разрастался и был тривиально mock-аем
    в тестах (test_reuses_24_3_function spies на runner).
    """
    from report_service.bundles.runner import run_bundle_job  # noqa: PLC0415

    job_uuid = UUID(job_id)
    sf = _make_session_factory()
    return await run_bundle_job(sf, job_id=job_uuid)


async def purge_expired_bundles_job(_ctx: dict[str, Any]) -> dict[str, Any]:
    """arq entry: TTL sweep — удаляет expired bundle row'ы + storage blobs.

    Делегирует в :func:`report_service.bundles.cleanup.purge_expired_bundles`.
    Запускается arq cron-ом раз в час (см. :class:`WorkerSettings`).
    """
    from report_service.bundles.cleanup import purge_expired_bundles  # noqa: PLC0415

    sf = _make_session_factory()
    purged = await purge_expired_bundles(sf)
    return {"purged": purged}


def _make_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazy ``async_sessionmaker`` — инициализирует engine при первом обращении."""
    settings = get_settings()
    try:
        engine = get_engine()
    except RuntimeError:
        engine = init_engine(settings.database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


class WorkerSettings:
    """arq WorkerSettings.

    arq читает class-level атрибуты (не инстанцирует). См. arq docs
    «WorkerSettings».
    """

    redis_settings: ClassVar[RedisSettings] = _redis_settings_from_env()
    queue_name: ClassVar[str] = QUEUE_NAME
    functions: ClassVar[list[Any]] = [
        generate_report_bundle_job,
        purge_expired_bundles_job,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        # Раз в час: TTL purge. Нагрузка минимальна (1 SELECT + N DELETE),
        # cron-расписание точное, не зависит от окна между restart'ами.
        cron(
            purge_expired_bundles_job,
            hour=set(range(24)),
            minute={0},
        ),
    ]


__all__ = [
    "QUEUE_NAME",
    "WorkerSettings",
    "generate_report_bundle_job",
    "purge_expired_bundles_job",
]
