"""FamilySearch import API: ``POST /imports/familysearch``.

Принимает ``{access_token, fs_person_id, tree_id, generations}``, тянет
pedigree из FamilySearch и заливает в ORM через
``familysearch_importer.import_fs_pedigree``. Синхронный режим (запрос
ждёт до завершения), как и legacy GEDCOM-импорт; background-режим через
``arq`` — Phase 3.5/5.2.

См. ADR-0017 для маппинга и ADR-0011 для клиента.

**Security note:** ``access_token`` приходит от пользователя
(после OAuth PKCE flow на их стороне) и **не сохраняется**. В лог идёт
только ``sha256(access_token)[:8]`` для traceability.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Annotated

from familysearch_client import (
    AuthError,
    ClientError,
    RateLimitError,
    ServerError,
)
from familysearch_client import (
    NotFoundError as FsNotFoundError,
)
from fastapi import APIRouter, Depends, HTTPException, status
from shared_models.enums import ImportJobStatus
from shared_models.orm import ImportJob, Tree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.schemas import FamilySearchImportRequest, ImportJobResponse
from parser_service.services.familysearch_importer import import_fs_pedigree

logger = logging.getLogger(__name__)

router = APIRouter()


def _token_fingerprint(access_token: str) -> str:
    """sha256(access_token)[:8] — для логов без раскрытия секрета."""
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:8]


@router.post(
    "/familysearch",
    response_model=ImportJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Импортировать FamilySearch person + N поколений предков",
)
async def create_familysearch_import(
    request: FamilySearchImportRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    """Импорт FS pedigree в существующее дерево.

    Маппинг FS GEDCOM-X → ORM — см. ADR-0017. Идемпотентность:
    повторный запрос с тем же ``fs_person_id`` обновит существующих
    persons, не создаст дубликаты.
    """
    # Verify tree exists. caller should already have authenticated and
    # verified ownership; для Phase 5.1 ownership check вынесен в
    # auth-middleware (Phase 4.x), здесь только sanity-check.
    tree = (
        await session.execute(select(Tree).where(Tree.id == request.tree_id))
    ).scalar_one_or_none()
    if tree is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {request.tree_id} not found",
        )

    logger.info(
        "FS import requested: fs_person_id=%s tree_id=%s generations=%d token_fp=%s",
        request.fs_person_id,
        request.tree_id,
        request.generations,
        _token_fingerprint(request.access_token),
    )

    try:
        job = await import_fs_pedigree(
            session,
            access_token=request.access_token,
            fs_person_id=request.fs_person_id,
            tree_id=request.tree_id,
            owner_user_id=tree.owner_user_id,
            generations=request.generations,
        )
    except FsNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FamilySearch person {request.fs_person_id} not found",
        ) from e
    except AuthError as e:
        # 401 от FS = битый/просроченный токен (наш FS, не наш user).
        # Возвращаем 401, чтобы фронт инициировал re-auth-flow.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"FamilySearch rejected access token: {e}",
        ) from e
    except RateLimitError as e:
        retry_after = int(e.retry_after) if e.retry_after is not None else None
        headers: dict[str, str] = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="FamilySearch rate limit exceeded",
            headers=headers,
        ) from e
    except ServerError as e:
        # FS down/overloaded — сообщаем 502 (наш upstream — FS).
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FamilySearch upstream error: {e}",
        ) from e
    except ClientError as e:
        # 400/422 от FS — обычно невалидный запрос от нашей стороны.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FamilySearch client error: {e}",
        ) from e

    return ImportJobResponse.model_validate(job)


@router.get(
    "/familysearch/{job_id}",
    response_model=ImportJobResponse,
    summary="Получить статус FS-импорта",
)
async def get_familysearch_import(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    """Повторно загрузить ``ImportJob`` (для polling-сценариев фронта).

    Не отличается семантически от ``GET /imports/{id}`` — sugar-endpoint
    для удобства, чтобы фронт мог гонять ``/imports/familysearch/{id}``
    в той же области URL, что и POST.
    """
    job = (
        await session.execute(select(ImportJob).where(ImportJob.id == job_id))
    ).scalar_one_or_none()
    if job is None or job.source_kind != "familysearch":
        # 404 на «не наш» kind — чтобы fs-frontend не получал GEDCOM-jobs
        # под видом FS-job'ов.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FamilySearch import job {job_id} not found",
        )
    if job.status == ImportJobStatus.RUNNING.value:
        # Сообщаем 200 + актуальные stats (могут быть пустыми).
        pass
    return ImportJobResponse.model_validate(job)
