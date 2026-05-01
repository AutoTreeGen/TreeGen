"""Audio consent API (Phase 10.9a / ADR-0064 §B1).

Per-tree privacy-gate под voice-to-tree. Owner явно opt-in'ит дерево
на egress аудио в STT-провайдер; до этого ``POST /audio-sessions``
вернёт 403 ``consent_required``.

Эндпоинты:

* ``GET    /trees/{tree_id}/audio-consent`` — VIEWER, читает текущее состояние.
* ``POST   /trees/{tree_id}/audio-consent`` — OWNER, idempotent set.
* ``DELETE /trees/{tree_id}/audio-consent`` — OWNER, revoke + enqueue
  erasure-job для каждой неудалённой ``audio_sessions``-row дерева
  (см. ADR-0049 паттерн hard-delete on revoke).

Контракт ролей соответствует ADR-0036: consent — owner-level decision,
не editor'ский (даже EDITOR не должен переключать privacy-tier за
владельца).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, status
from shared_models import TreeRole
from shared_models.orm import AudioSession, Tree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.queue import get_arq_pool
from parser_service.schemas import (
    AudioConsentRequest,
    AudioConsentResponse,
    AudioConsentRevokeResponse,
)
from parser_service.services.permissions import require_tree_role

# Имя arq-функции, которое регистрирует worker (см. ``parser_service.worker``).
# Захардкожено как строковая константа — endpoint не импортирует worker
# модуль напрямую (cross-package boundary; worker сам импортирует ai-layer
# и тяжёлые deps, которые HTTP-слою не нужны).
ERASE_AUDIO_SESSION_JOB_NAME = "erase_audio_session"

router = APIRouter()


async def _fetch_tree_or_404(session: AsyncSession, tree_id: uuid.UUID) -> Tree:
    """Загрузить ``Tree`` row или поднять 404.

    Permission gate (``require_tree_role``) уже проверил существование
    дерева до того, как сюда попали — но между gate и handler'ом
    транзакция могла «увидеть» удаление; explicit 404 надёжнее, чем
    падение на ``NoneType``.
    """
    tree = await session.get(Tree, tree_id)
    if tree is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {tree_id} not found",
        )
    return tree


@router.get(
    "/trees/{tree_id}/audio-consent",
    response_model=AudioConsentResponse,
    summary="Read current voice-egress consent state for a tree (VIEWER+).",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_audio_consent(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AudioConsentResponse:
    """Вернуть текущее значение consent-полей дерева.

    Если consent не дан — оба поля ``null`` (consumer'ы UI рисуют CTA
    «Enable voice transcription»). VIEWER достаточно: знание о наличии
    consent'а — read-level info, не privileged.
    """
    tree = await _fetch_tree_or_404(session, tree_id)
    return AudioConsentResponse(
        tree_id=tree.id,
        audio_consent_egress_at=tree.audio_consent_egress_at,
        audio_consent_egress_provider=tree.audio_consent_egress_provider,
    )


@router.post(
    "/trees/{tree_id}/audio-consent",
    response_model=AudioConsentResponse,
    summary="Owner-only — grant voice-egress consent (idempotent).",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def set_audio_consent(
    tree_id: uuid.UUID,
    body: AudioConsentRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AudioConsentResponse:
    """Пометить дерево как opt-in'нутое в voice-egress.

    Идемпотентно: если consent уже set (``audio_consent_egress_at IS NOT
    NULL``) — НЕ обновляем timestamp, возвращаем существующее значение.
    Это критично для provenance: ``AudioSession.consent_egress_at``
    snapshot'ит этот timestamp на момент upload'а; перезапись здесь
    «откатила» бы привязку существующих сессий к более раннему consent'у.

    Смена провайдера (например, ``openai`` → ``self-hosted-whisper``)
    требует явного DELETE → POST: revoke удалит существующие сессии
    (они были даны под старого провайдера), новый POST создаст свежий
    timestamp.

    Returns:
        :class:`AudioConsentResponse` — текущее (или только что set'нутое) состояние.
    """
    tree = await _fetch_tree_or_404(session, tree_id)

    if tree.audio_consent_egress_at is not None:
        # Idempotency: не перезаписываем. Provider-mismatch — 409 чтобы
        # caller явно сделал revoke перед сменой backend'а.
        if tree.audio_consent_egress_provider != body.provider:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Consent already granted for provider "
                    f"{tree.audio_consent_egress_provider!r}; revoke first "
                    f"before switching to {body.provider!r}."
                ),
            )
        return AudioConsentResponse(
            tree_id=tree.id,
            audio_consent_egress_at=tree.audio_consent_egress_at,
            audio_consent_egress_provider=tree.audio_consent_egress_provider,
        )

    tree.audio_consent_egress_at = dt.datetime.now(dt.UTC)
    tree.audio_consent_egress_provider = body.provider
    await session.flush()

    return AudioConsentResponse(
        tree_id=tree.id,
        audio_consent_egress_at=tree.audio_consent_egress_at,
        audio_consent_egress_provider=tree.audio_consent_egress_provider,
    )


@router.delete(
    "/trees/{tree_id}/audio-consent",
    response_model=AudioConsentRevokeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Owner-only — revoke voice-egress consent + enqueue erasure for all sessions.",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def revoke_audio_consent(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> AudioConsentRevokeResponse:
    """Снять consent + поставить erasure-job на каждую активную сессию.

    202 Accepted: handler НЕ ждёт завершения erasure (см. ADR-0064 §F1
    + ADR-0049 паттерн async-erasure). UI получает список enqueued
    session_ids и опрашивает ``GET /trees/{id}/audio-sessions`` пока
    список не опустеет.

    Idempotency: повторный DELETE на дереве без consent'а или без
    активных сессий — успешно возвращает 202 с пустым списком.
    """
    tree = await _fetch_tree_or_404(session, tree_id)

    # Сбрасываем consent-поля атомарно с enqueue'ем — если транзакция
    # упадёт после flush'а, jobs всё равно поставятся, но они проверят
    # session.deleted_at IS NOT NULL и no-op'нут (см. erase worker).
    revoked_at = dt.datetime.now(dt.UTC)
    tree.audio_consent_egress_at = None
    tree.audio_consent_egress_provider = None

    # Активные (не soft-deleted) сессии дерева. Hard-deleted уже не
    # существуют на DB-уровне — их erasure делать не нужно.
    res = await session.execute(
        select(AudioSession.id).where(
            AudioSession.tree_id == tree_id,
            AudioSession.deleted_at.is_(None),
        )
    )
    session_ids = list(res.scalars().all())
    await session.flush()

    # Enqueue по одной job на сессию: erasure-pipeline в ADR-0049
    # idempotent per-row, проще чем bulk-job, и каждая записывает
    # отдельную gdpr_erasure_log запись для аудита.
    for sid in session_ids:
        await pool.enqueue_job(
            ERASE_AUDIO_SESSION_JOB_NAME,
            str(sid),
            _job_id=f"erase_audio_session:{sid}",
        )

    return AudioConsentRevokeResponse(
        tree_id=tree_id,
        revoked_at=revoked_at,
        enqueued_session_ids=session_ids,
    )


__all__ = ["ERASE_AUDIO_SESSION_JOB_NAME", "router"]
