"""arq job: 3-pass voice extraction over one ``AudioSession`` (Phase 10.9b).

Лёгкая обёртка вокруг :class:`VoiceExtractor`:

1. Загрузить session по ``session_id``, проверить privacy + transcript.
2. Запустить 3-pass extraction'а (soft-fail внутри — не raise).
3. Persist proposals batch'ем в ``voice_extracted_proposals``.
4. Записать ``provenance.job_status`` (``succeeded`` / ``partial_failed`` /
   ``cost_capped`` / ``failed``) в каждом proposal'е (group-by идёт по
   extraction_job_id).

Idempotency: если для (session_id, extraction_job_id) уже есть proposals —
no-op (duplicate enqueue или retry уже-выполненной job).

Cost-cap (``VoiceExtractCostCapError``) → нет proposals в БД, status в логе.
Worker не пишет ничего — caller (POST endpoint) уже отдал 202 с тем же
``extraction_job_id``; UI получит пустой group через
``GET /audio-sessions/{id}/extractions`` (job просто не появится в списке).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.config import AILayerConfig
from ai_layer.use_cases.voice_to_tree_extract import (
    VoiceExtractCostCapError,
    VoiceExtractInput,
    VoiceExtractor,
)
from shared_models.orm import (
    AudioSession,
    VoiceExtractedProposal,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from parser_service.database import get_engine

_logger = logging.getLogger(__name__)


def _build_extractor() -> tuple[VoiceExtractor, AILayerConfig]:
    """Сконструировать :class:`VoiceExtractor` + вернуть AILayerConfig.

    Per-job (lifecycle = job, not process) — Anthropic SDK-клиент не
    шарится между event-loop'ами без специального handling'а; per-job
    дёшево (см. transcribe_audio job). Конфиг читается из ENV — тот же
    путь что в ai_extraction.py / chat.py / normalize.py.
    """
    config = AILayerConfig.from_env()
    return VoiceExtractor(AnthropicClient(config)), config


async def voice_extract_job(
    ctx: dict[str, Any],
    session_id: str,
    extraction_job_id: str,
) -> dict[str, Any]:
    """arq job: 3-pass NLU extraction.

    Args:
        ctx: arq-контекст; ``ctx['redis']`` для telemetry.
        session_id: UUID-string AudioSession (transcript уже ready).
        extraction_job_id: UUID-string, выданный в POST endpoint'е;
            используется как :class:`VoiceExtractor` job-id-grouper и
            как arq idempotency-key.

    Returns:
        Sterile dict для arq-result: ``session_id``, ``extraction_job_id``,
        ``status``, ``proposals_count``, ``error`` (опционально).
    """
    redis_client = ctx.get("redis")
    extractor, config = _build_extractor()

    session_uuid = UUID(session_id)
    job_uuid = UUID(extraction_job_id)

    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # 1. Load + privacy check + idempotency check.
    async with session_maker() as ds:
        session = await ds.get(AudioSession, session_uuid)
        if session is None:
            msg = f"AudioSession {session_id} not found"
            raise LookupError(msg)
        if session.consent_egress_at is None:
            return {
                "session_id": session_id,
                "extraction_job_id": extraction_job_id,
                "status": "failed",
                "error": "consent_required",
            }
        if session.status != "ready" or not session.transcript_text:
            return {
                "session_id": session_id,
                "extraction_job_id": extraction_job_id,
                "status": "failed",
                "error": f"transcript_not_ready:status={session.status}",
            }

        # Idempotency: если уже есть proposals для этой пары — no-op.
        existing = await ds.execute(
            select(VoiceExtractedProposal.id)
            .where(
                VoiceExtractedProposal.audio_session_id == session_uuid,
                VoiceExtractedProposal.extraction_job_id == job_uuid,
            )
            .limit(1)
        )
        if existing.first() is not None:
            return {
                "session_id": session_id,
                "extraction_job_id": extraction_job_id,
                "status": "skipped",
                "error": "duplicate_job",
            }

        transcript_text = session.transcript_text
        tree_id = session.tree_id
        owner_user_id = session.owner_user_id
        language = session.language

    # 2. Extract (soft-fail внутри VoiceExtractor — не raise per pass).
    try:
        result = await extractor.run(
            VoiceExtractInput(
                transcript_text=transcript_text,
                language=language,
            ),
            redis=redis_client,
            user_id=owner_user_id,
        )
    except VoiceExtractCostCapError as exc:
        # Pre-flight cap превышен — не сохраняем ничего, status в логе.
        _logger.warning(
            "voice_extract %s cost-capped pre-flight: %s",
            extraction_job_id,
            exc,
        )
        return {
            "session_id": session_id,
            "extraction_job_id": extraction_job_id,
            "status": "cost_capped",
            "proposals_count": 0,
            "error": str(exc),
        }
    except Exception as exc:
        _logger.exception(
            "voice_extract %s unexpected failure",
            extraction_job_id,
        )
        return {
            "session_id": session_id,
            "extraction_job_id": extraction_job_id,
            "status": "failed",
            "proposals_count": 0,
            "error": f"unexpected:{type(exc).__name__}:{exc}",
        }

    # extraction_job_id из POST'а должен победить тот, что VoiceExtractor
    # сгенерил сам — внешний контракт с UI/idempotency-key'ем единый.
    persisted_job_id = job_uuid

    # 3. Persist proposals batch'ем.
    if result.proposals:
        async with session_maker() as ds:
            for proposal in result.proposals:
                row = VoiceExtractedProposal(
                    tree_id=tree_id,
                    audio_session_id=session_uuid,
                    extraction_job_id=persisted_job_id,
                    proposal_type=proposal.proposal_type,
                    pass_number=proposal.pass_number,
                    status="pending",
                    payload=proposal.payload,
                    confidence=proposal.confidence,
                    evidence_snippets=proposal.evidence_snippets,
                    raw_response=proposal.raw_tool_call,
                    model_version=(
                        result.passes[proposal.pass_number - 1].model
                        if result.passes and len(result.passes) >= proposal.pass_number
                        else config.anthropic_model
                    ),
                    prompt_version=f"voice_extract_pass{proposal.pass_number}_v1",
                    input_tokens=(
                        result.passes[proposal.pass_number - 1].input_tokens
                        if result.passes and len(result.passes) >= proposal.pass_number
                        else 0
                    ),
                    output_tokens=(
                        result.passes[proposal.pass_number - 1].output_tokens
                        if result.passes and len(result.passes) >= proposal.pass_number
                        else 0
                    ),
                    cost_usd=(
                        result.passes[proposal.pass_number - 1].cost_usd
                        if result.passes and len(result.passes) >= proposal.pass_number
                        else 0
                    ),
                    provenance={
                        "job_status": result.status,
                        "job_request_user_id": str(owner_user_id),
                        "language_hint": language or "auto",
                    },
                )
                ds.add(row)
            await ds.commit()

    return {
        "session_id": session_id,
        "extraction_job_id": extraction_job_id,
        "status": result.status,
        "proposals_count": len(result.proposals),
        "error": result.error_message,
    }


__all__ = ["voice_extract_job"]
