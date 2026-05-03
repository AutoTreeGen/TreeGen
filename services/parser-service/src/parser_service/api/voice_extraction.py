"""Voice extraction API (Phase 10.9b / ADR-0075).

Endpoints:

* ``POST /audio-sessions/{id}/extract`` — EDITOR-only. Триггер 3-pass
  NLU extraction'а. Privacy-gate: 403 ``consent_required`` если
  ``consent_egress_at IS NULL`` на session (Anthropic — тоже egress, тот
  же gate что у Whisper в 10.9a). 409 ``transcript_not_ready`` если
  status != 'ready'. Идемпотентен: если уже есть active job — возвращает
  существующий ``extraction_job_id`` (если ``force=false``).

* ``GET  /audio-sessions/{id}/extractions`` — VIEWER. Все proposals
  session, group-by ``extraction_job_id``.

* ``GET  /extractions/{extraction_job_id}`` — VIEWER. Proposals одного
  job'а (для review-queue в 10.9c).

Permission gates: ``audio_sessions/{id}`` → resolve session→tree → role check
(зеркало паттерна ``get_audio_session`` из ``audio_sessions.py``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Final

from ai_layer import AILayerConfig
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, status
from shared_models import TreeRole
from shared_models.orm import AudioSession, Tree, VoiceExtractedProposal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import RequireUser
from parser_service.database import get_session
from parser_service.queue import get_arq_pool
from parser_service.schemas import (
    ExtractionJobDetailResponse,
    ExtractionJobResponse,
    ExtractionsByJobItem,
    ExtractionsBySessionResponse,
    StartExtractionRequest,
    VoiceExtractedProposalResponse,
)
from parser_service.services.permissions import check_tree_permission

# Имя arq-функции — должно совпадать с ``parser_service.jobs.voice_extract``.
VOICE_EXTRACT_JOB_NAME: Final[str] = "voice_extract_job"

router = APIRouter()


def _proposal_to_response(row: VoiceExtractedProposal) -> VoiceExtractedProposalResponse:
    """ORM → DTO: упрощает `.dict()` через model_validate с from_attributes."""
    return VoiceExtractedProposalResponse.model_validate(row)


async def _require_session_role(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    db_session: AsyncSession,
    required: TreeRole,
) -> AudioSession:
    """Resolve session→tree → role check.

    404 для cross-tree user'а (privacy: не различаем «нет session» от
    «нет доступа», как в ``get_audio_session``).
    """
    row = await db_session.get(AudioSession, session_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AudioSession {session_id} not found",
        )
    has_role = await check_tree_permission(
        db_session,
        user_id=user_id,
        tree_id=row.tree_id,
        required=required,
    )
    if not has_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AudioSession {session_id} not found",
        )
    return row


@router.post(
    "/audio-sessions/{session_id}/extract",
    response_model=ExtractionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Editor-only — trigger 3-pass NLU extraction over transcript.",
)
async def start_extraction(
    session_id: uuid.UUID,
    body: StartExtractionRequest,
    user_id: RequireUser,
    db_session: Annotated[AsyncSession, Depends(get_session)],
    pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> ExtractionJobResponse:
    """Поставить voice_extract_job в очередь.

    Защита в три слоя:
    1. **Permission** — EDITOR на дереве сессии (resolve session→tree).
    2. **Consent gate** — ``session.consent_egress_at IS NULL`` →
       403 ``consent_required``. Anthropic = тот же egress-канал что и
       Whisper, тот же gate (ADR-0064 §3.6 + ADR-0075 §«Privacy»).
    3. **Transcript readiness** — ``session.status != 'ready'`` или
       ``transcript_text is None`` → 409 ``transcript_not_ready``.

    Затем: extraction_job_id (UUID) + enqueue arq-job + 202 Accepted.
    """
    session = await _require_session_role(
        session_id=session_id,
        user_id=user_id,
        db_session=db_session,
        required=TreeRole.EDITOR,
    )

    # Privacy-gate. session.consent_egress_at — immutable snapshot (NOT NULL
    # на DB-уровне), но tree-level consent может быть отозван между записью
    # и extraction'ом. Проверяем CURRENT tree consent — если revoked → 403,
    # даже если у session snapshot ещё есть. Anthropic = тот же egress что
    # Whisper (ADR-0064 §3.6).
    tree = await db_session.get(Tree, session.tree_id)
    if (
        tree is None
        or tree.audio_consent_egress_at is None
        or tree.audio_consent_egress_provider is None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "consent_required",
                "session_id": str(session_id),
                "tree_id": str(session.tree_id),
                "message": (
                    "Voice egress consent has been revoked on this tree "
                    "or was never granted. Re-grant consent before extraction."
                ),
            },
        )

    # Transcript readiness.
    if session.status != "ready" or not session.transcript_text:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "transcript_not_ready",
                "session_id": str(session_id),
                "session_status": session.status,
                "message": (
                    "Transcript is not ready (session status must be 'ready' "
                    "with non-empty transcript_text)."
                ),
            },
        )

    # AI availability fail-fast: AILayerConfig зеркалит остальные ai-endpoint'ы
    # (ai_extraction.py, chat.py, normalize.py).
    config = AILayerConfig.from_env()
    if not config.enabled or not config.anthropic_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "nlu_unavailable",
                "message": (
                    "NLU provider is not configured (AI_LAYER_ENABLED=false or "
                    "ANTHROPIC_API_KEY missing)."
                ),
            },
        )

    # Idempotency: если у сессии уже есть proposals и force=False —
    # возвращаем существующий extraction_job_id (берём latest по created_at).
    if not body.force:
        existing = await db_session.execute(
            select(
                VoiceExtractedProposal.extraction_job_id,
                VoiceExtractedProposal.created_at,
            )
            .where(VoiceExtractedProposal.audio_session_id == session_id)
            .order_by(VoiceExtractedProposal.created_at.desc())
            .limit(1)
        )
        row = existing.first()
        if row is not None:
            existing_job_id, existing_started = row
            return ExtractionJobResponse(
                extraction_job_id=existing_job_id,
                audio_session_id=session_id,
                status="succeeded",  # есть proposals → было запущено хотя бы раз
                created_at=existing_started,
            )

    extraction_job_id = uuid.uuid4()
    await pool.enqueue_job(
        VOICE_EXTRACT_JOB_NAME,
        str(session_id),
        str(extraction_job_id),
        _job_id=f"voice_extract:{extraction_job_id}",
    )

    return ExtractionJobResponse(
        extraction_job_id=extraction_job_id,
        audio_session_id=session_id,
        status="queued",
        created_at=dt.datetime.now(dt.UTC),
    )


@router.get(
    "/audio-sessions/{session_id}/extractions",
    response_model=ExtractionsBySessionResponse,
    summary="Viewer-only — list extraction jobs for a session, grouped by job-id.",
)
async def list_extractions_for_session(
    session_id: uuid.UUID,
    user_id: RequireUser,
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> ExtractionsBySessionResponse:
    """Все proposals session'а group-by ``extraction_job_id``.

    Caller (10.9c review-UI) показывает каждый job отдельной group'ой
    с aggregated status (job-status одинаковый для всех rows одного job'а).
    """
    await _require_session_role(
        session_id=session_id,
        user_id=user_id,
        db_session=db_session,
        required=TreeRole.VIEWER,
    )

    rows_result = await db_session.execute(
        select(VoiceExtractedProposal)
        .where(VoiceExtractedProposal.audio_session_id == session_id)
        .order_by(
            VoiceExtractedProposal.extraction_job_id,
            VoiceExtractedProposal.pass_number,
            VoiceExtractedProposal.created_at,
        )
    )
    rows = list(rows_result.scalars().all())

    # group-by extraction_job_id, preserving order.
    grouped: dict[uuid.UUID, list[VoiceExtractedProposal]] = {}
    for row in rows:
        grouped.setdefault(row.extraction_job_id, []).append(row)

    items: list[ExtractionsByJobItem] = []
    for job_id, group in grouped.items():
        first = group[0]
        job_status = first.provenance.get("job_status", "succeeded")
        items.append(
            ExtractionsByJobItem(
                extraction_job_id=job_id,
                status=job_status,
                proposals_total=len(group),
                started_at=first.created_at,
                proposals=[_proposal_to_response(p) for p in group],
            )
        )

    return ExtractionsBySessionResponse(
        audio_session_id=session_id,
        total_jobs=len(items),
        jobs=items,
    )


@router.get(
    "/extractions/{extraction_job_id}",
    response_model=ExtractionJobDetailResponse,
    summary="Viewer-only — proposals of a single extraction job (review queue).",
)
async def get_extraction_job(
    extraction_job_id: uuid.UUID,
    user_id: RequireUser,
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> ExtractionJobDetailResponse:
    """Один extraction_job по UUID. Permission resolved через session→tree."""
    rows_result = await db_session.execute(
        select(VoiceExtractedProposal)
        .where(VoiceExtractedProposal.extraction_job_id == extraction_job_id)
        .order_by(
            VoiceExtractedProposal.pass_number,
            VoiceExtractedProposal.created_at,
        )
    )
    rows = list(rows_result.scalars().all())
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extraction job {extraction_job_id} not found",
        )

    # Permission: все proposals одного job'а лежат в одной session/tree.
    session_id = rows[0].audio_session_id
    await _require_session_role(
        session_id=session_id,
        user_id=user_id,
        db_session=db_session,
        required=TreeRole.VIEWER,
    )

    job_status = rows[0].provenance.get("job_status", "succeeded")
    return ExtractionJobDetailResponse(
        extraction_job_id=extraction_job_id,
        audio_session_id=session_id,
        status=job_status,
        proposals_total=len(rows),
        proposals=[_proposal_to_response(r) for r in rows],
    )


__all__ = ["VOICE_EXTRACT_JOB_NAME", "router"]
