"""Imports API: ``POST /imports`` (async-enqueue) + ``GET /imports/{id}`` + cancel.

Phase 3.5 — переход с синхронного импорта на arq-очередь.

* ``POST /imports`` персистит ``ImportJob(status=queued)``, сохраняет
  загруженный файл в tmp и enqueue'ит ``run_import_job(job_id, tmp_path)``.
  Возвращает 202 Accepted с ``id`` и ``events_url`` (SSE).
* ``GET /imports/{id}`` отдаёт текущий статус + последний ``progress``
  снапшот (worker обновляет в ``ImportJob.progress`` jsonb).
* ``PATCH /imports/{id}/cancel`` ставит ``cancel_requested=True``;
  worker увидит флаг между стадиями и переведёт status → cancelled.

Voucher: пока ``run_import_job`` не зарегистрирован в worker'е (см.
зависимый PR ``feat/phase-3.5-arq-worker``), enqueue физически кладёт
job в очередь, но никто его не процессит. Это не делает endpoint
бесполезным для smoke-тестов — мокаем pool в pytest-фикстуре. См.
ADR-0026 для координации между PR'ами Phase 3.5.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from shared_models.enums import (
    ImportJobStatus,
    ImportSourceKind,
    TreeVisibility,
)
from shared_models.orm import ImportJob, Tree, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.queue import get_arq_pool
from parser_service.schemas import ImportJobResponse

router = APIRouter()

# Имя arq-функции, которую вызывает worker. Регистрируется в worker'е
# зависимого PR (feat/phase-3.5-arq-worker). Захардкожено как строковая
# константа — чтобы не плодить cross-package imports на pre-merge стадии.
RUN_IMPORT_JOB_NAME = "run_import_job"

# Шаблон относительного URL SSE-эндпоинта, возвращаемого в 202 Accepted.
# Полный путь монтируется в main.py через ``app.include_router(... prefix="/imports")``.
_EVENTS_URL_TEMPLATE = "/imports/{job_id}/events"


def _events_url(job_id: uuid.UUID) -> str:
    """Сформировать относительный URL SSE-эндпоинта для конкретного job."""
    return _EVENTS_URL_TEMPLATE.format(job_id=job_id)


def _job_to_response(job: ImportJob, *, events_url: str | None = None) -> ImportJobResponse:
    """Сконвертировать ORM ImportJob в HTTP-ответ.

    ``events_url`` передаётся только из POST/PATCH: на GET он не нужен,
    т.к. UI уже подключён к SSE до того, как делает poll.
    """
    payload = ImportJobResponse.model_validate(job)
    if events_url is not None:
        payload = payload.model_copy(update={"events_url": events_url})
    return payload


async def _ensure_owner(session: AsyncSession, email: str) -> User:
    """Найти существующего user по email или создать нового.

    Дублирует логику ``import_runner._ensure_owner`` — оставляем здесь
    отдельно, потому что pre-runner шаг создаёт ``Tree`` сразу при
    POST /imports (нужно ``tree_id`` на job ещё до того, как воркер
    запустится).
    """
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        external_auth_id=f"local:{email}",
        display_name=email.split("@", maxsplit=1)[0],
        locale="en",
    )
    session.add(user)
    await session.flush()
    return user


@router.post(
    "",
    response_model=ImportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue async-импорт GEDCOM-файла",
)
async def create_import(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> ImportJobResponse:
    """Принять multipart upload .ged → персистнуть job + enqueue worker.

    Загружаемый файл валидируется по расширению и размеру, затем
    сохраняется в tempfile (worker позже его прочитает по path'у). Tree
    создаётся синхронно — нужен ``tree_id`` для ``ImportJob`` row'а.
    Сам импорт (парсинг + bulk insert + audit-log) делает arq worker
    через зарегистрированную функцию ``run_import_job(ctx, job_id, tmp_path)``.

    Returns 202 с ``id`` и ``events_url`` — UI подключается к SSE и
    показывает live-прогресс до терминальной стадии.
    """
    if not file.filename or not file.filename.lower().endswith((".ged", ".gedcom")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected .ged or .gedcom file",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    contents = await file.read()
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large; max {settings.max_upload_mb} MB",
        )

    # Tempfile живёт между запросом API и worker'ом. Worker отвечает за
    # cleanup (см. зависимый PR runner'а). Если worker никогда не
    # стартовал — оставшиеся tmp-файлы подметаются janitor-cron'ом
    # (Phase 3.5+).
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ged") as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    owner = await _ensure_owner(session, settings.owner_email)

    # Tree создаётся сразу: ImportJob.tree_id NOT NULL FK. Worker позже
    # дополняет provenance после успешного парсинга.
    tree = Tree(
        owner_user_id=owner.id,
        name=Path(file.filename).stem,
        visibility=TreeVisibility.PRIVATE.value,
        default_locale="en",
        settings={},
        provenance={"source_filename": file.filename},
        version_id=1,
    )
    session.add(tree)
    await session.flush()

    job = ImportJob(
        tree_id=tree.id,
        created_by_user_id=owner.id,
        source_kind=ImportSourceKind.GEDCOM.value,
        source_filename=file.filename,
        source_size_bytes=len(contents),
        status=ImportJobStatus.QUEUED.value,
        # Явно проставляем jsonb-дефолты: column-level ``default=dict``/
        # ``default=list`` срабатывает на flush в реальный engine, но
        # in-memory тесты используют stub-session без flush'а — поле
        # бы осталось ``None`` и Pydantic-валидация ответа упала бы.
        stats={},
        errors=[],
        progress=None,
        cancel_requested=False,
    )
    session.add(job)
    await session.flush()

    # Enqueue arq job. Хвост `_queue_name` фиксирует очередь явно —
    # совпадает с настройкой воркера. Args сериализуются как msgpack;
    # передаём UUID-строкой (msgpack не знает UUID нативно), tmp_path
    # — обычной строкой. Worker заберёт оба и реконструирует typed.
    await pool.enqueue_job(
        RUN_IMPORT_JOB_NAME,
        str(job.id),
        str(tmp_path),
        _queue_name=settings.arq_queue_name,
    )

    return _job_to_response(job, events_url=_events_url(job.id))


@router.get(
    "/{job_id}",
    response_model=ImportJobResponse,
    summary="Получить текущий статус + progress импорта",
)
async def get_import(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    """Возвращает статус, stats и последний снапшот ``progress``.

    Используется UI как fallback для случаев когда SSE недоступен
    (firewall / закрыли таб) — polling каждые N секунд видит тот же
    последний снапшот, что и SSE.
    """
    res = await session.execute(select(ImportJob).where(ImportJob.id == job_id))
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import job {job_id} not found",
        )
    return _job_to_response(job)


@router.patch(
    "/{job_id}/cancel",
    response_model=ImportJobResponse,
    summary="Запросить graceful cancel импорта",
)
async def request_cancel_import(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    """Выставить ``cancel_requested=True``.

    Worker проверяет флаг между стадиями и переводит status → cancelled.
    Для уже терминальных job'ов (succeeded / failed / cancelled / partial) —
    no-op (200 + текущий state). Идея зеркалит
    ``PATCH /hypotheses/compute-jobs/{id}/cancel`` Phase 7.5.
    """
    res = await session.execute(select(ImportJob).where(ImportJob.id == job_id))
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import job {job_id} not found",
        )

    terminal_statuses = {
        ImportJobStatus.SUCCEEDED.value,
        ImportJobStatus.FAILED.value,
        ImportJobStatus.CANCELLED.value,
        ImportJobStatus.PARTIAL.value,
    }
    if job.status in terminal_statuses:
        # Идемпотентный no-op — UI не должен 4xx'иться, если зашёл
        # «отмени» уже на завершённый job.
        return _job_to_response(job)

    job.cancel_requested = True
    await session.flush()
    return _job_to_response(job, events_url=_events_url(job.id))
