"""Imports API: ``POST /imports`` + ``GET /imports/{id}``.

В этой итерации импорт **синхронный** — запрос ждёт до завершения парсинга
и записи. Background-режим через arq — Phase 3.5.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from shared_models.orm import ImportJob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.schemas import ImportJobResponse
from parser_service.services.import_runner import run_import

router = APIRouter()


@router.post(
    "",
    response_model=ImportJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить и импортировать GEDCOM-файл",
)
async def create_import(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    """Принять multipart upload .ged → распарсить → залить в БД.

    Размер ограничен ``settings.max_upload_mb``. Файл временно сохраняется на
    диск (TempDir), затем парсер читает с диска. Логика парсинга и записи —
    в ``import_runner.run_import``.
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

    # Временный файл — gedcom-parser работает с путями, не с in-memory bytes.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ged") as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        job = await run_import(
            session,
            tmp_path,
            owner_email=settings.owner_email,
            tree_name=Path(file.filename).stem,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {e}",
        ) from e
    finally:
        tmp_path.unlink(missing_ok=True)

    return ImportJobResponse.model_validate(job)


@router.get(
    "/{job_id}",
    response_model=ImportJobResponse,
    summary="Получить статус job'а импорта",
)
async def get_import(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    """Возвращает текущий статус и stats импорта."""
    res = await session.execute(select(ImportJob).where(ImportJob.id == job_id))
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import job {job_id} not found",
        )
    return ImportJobResponse.model_validate(job)
