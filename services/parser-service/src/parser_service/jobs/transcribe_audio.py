"""arq job: транскрипция одной ``AudioSession`` через Whisper STT.

Phase 10.9a / ADR-0064 §G1 (soft-fail). Каждый шаг lifecycle-state'а —
один transition в ``AudioSession.status``:

::

    uploaded → transcribing → ready | failed

Идемпотентность: job ловит non-``uploaded`` сессию и возвращает no-op.
Это покрывает duplicate-enqueue (arq ``_job_id`` дедупит на стороне
очереди, но safety-net на app-уровне).

На любую ошибку Whisper-клиента: ``AudioTranscriber.run()`` сам
конвертирует в ``TranscribeAudioOutput.error`` (категория + message),
worker записывает её в ``error_message`` и ставит ``status=failed``.
arq retry-budget остаётся (см. ``WorkerSettings.functions``: 3 попытки,
экспоненциальный backoff). Для ``WhisperConfigError`` (нет ключа +
не dry-run) retry бесполезен — но мы всё равно возвращаем нормально,
arq не знает что fatal vs transient; user увидит ``failed`` в UI.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from ai_layer.clients.whisper import WhisperClient
from ai_layer.use_cases.transcribe_audio import (
    AudioTranscriber,
    TranscribeAudioInput,
)
from shared_models.orm import AudioSession
from shared_models.storage import ObjectStorage
from sqlalchemy.ext.asyncio import async_sessionmaker

from parser_service.config import Settings, get_settings
from parser_service.database import get_engine
from parser_service.services.audio_storage import (
    audio_object_key,
    get_audio_storage,
)

_logger = logging.getLogger(__name__)


def _build_transcriber(settings: Settings) -> AudioTranscriber:
    """Сконструировать ``AudioTranscriber`` из settings.

    Каждый вызов — свой WhisperClient (lifecycle worker-job, не процесса):
    boto3/openai SDK-клиенты не разделяемы между event-loop'ами без
    специального handling'а. Per-job дёшево; альтернатива — module-level
    singleton с asyncio.Lock — overkill для текущего throughput'а.
    """
    return AudioTranscriber(
        WhisperClient(
            api_key=settings.openai_api_key,
            max_duration_sec=settings.whisper_max_duration_sec,
        )
    )


async def transcribe_audio_session(
    ctx: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """arq job: транскрипция ``AudioSession`` через Whisper.

    Steps:
        1. Load row by ``session_id``. Проверить ``status='uploaded'``
           (idempotency — duplicate-enqueue / retry уже-выполненной job no-op).
        2. ``status='transcribing'`` + commit (UI видит progress).
        3. Read blob из storage по ``audio_object_key(...)``.
        4. ``AudioTranscriber.run(...)`` — soft-fail, возвращает output
           всегда (с ``error`` на failure).
        5. На success: заполнить transcript_*, status=``ready``.
           На failure: error_message = output.error, status=``failed``.
        6. Commit + return summary dict.

    Args:
        ctx: arq-контекст. ``ctx['redis']`` — async Redis-клиент для
            telemetry (передаётся в ``AudioTranscriber.run``).
        session_id: UUID-string существующей ``AudioSession``-row.

    Returns:
        Sterile dict для arq-result'а: ``session_id``, ``status``,
        ``cost_usd`` (если есть), ``error`` (если был fail).
    """
    redis_client = ctx.get("redis")
    settings = get_settings()
    storage: ObjectStorage = get_audio_storage(settings)
    transcriber = _build_transcriber(settings)

    session_uuid = UUID(session_id)
    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # Шаг 1: load + idempotency check. Открываем session-1 чтобы flip'нуть
    # status на ``transcribing`` отдельным commit'ом — UI/тесты увидят
    # переходное состояние, и при краше worker'а row не «зависнет» в
    # ``uploaded`` без признаков что мы её взяли.
    async with session_maker() as ds1:
        row = await ds1.get(AudioSession, session_uuid)
        if row is None:
            msg = f"AudioSession {session_id} not found"
            raise LookupError(msg)
        if row.deleted_at is not None:
            # Hard erasure-job уже удалил blob и пометил soft-delete →
            # transcribe не нужна. Возвращаем no-op.
            return {
                "session_id": session_id,
                "status": str(row.status),
                "skipped": "deleted",
            }
        if row.status != "uploaded":
            # Уже либо in-progress (другой worker подобрал), либо терминальная.
            return {
                "session_id": session_id,
                "status": str(row.status),
                "skipped": "non_uploaded_status",
            }
        row.status = "transcribing"
        await ds1.commit()
        # Сохраняем нужные поля до закрытия session-1: ORM-row после
        # commit'а expire'нется, повторное обращение даст новую row из
        # session-2 ниже.
        mime_type = row.mime_type
        owner_user_id = row.owner_user_id
        language_hint = row.language

    # Шаг 3: чтение blob'а. Storage operation — вне DB-session'а, чтобы
    # не держать tx открытой на время сетевого вызова.
    key = audio_object_key(session_uuid, mime_type)
    try:
        audio_bytes = await storage.get(key)
    except FileNotFoundError as exc:
        # Blob исчез между upload'ом и worker'ом — частая ситуация при
        # параллельном revoke consent'а. Помечаем failed, не retry.
        return await _persist_failure(
            session_maker=session_maker,
            session_uuid=session_uuid,
            error=f"storage:blob_missing:{exc}",
        )

    # Шаг 4: транскрипция (soft-fail — не raises на API errors).
    output = await transcriber.run(
        TranscribeAudioInput(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            language_hint=language_hint,
        ),
        redis=redis_client,
        user_id=owner_user_id,
    )

    # Шаг 5+6: persist результат.
    async with session_maker() as ds2:
        row = await ds2.get(AudioSession, session_uuid)
        if row is None:  # pragma: no cover — между шагами 1 и 5 невозможно
            msg = f"AudioSession {session_id} vanished between steps"
            raise LookupError(msg)

        if output.error is None:
            row.status = "ready"
            row.transcript_text = output.transcript
            row.language = output.language or row.language
            row.duration_sec = output.duration_sec
            row.transcript_provider = output.provider
            row.transcript_model_version = output.model_version
            row.transcript_cost_usd = output.cost_usd
            row.error_message = None
        else:
            row.status = "failed"
            # 2000-char cap матчит DB-уровень String(2000); long stack'и
            # урезаются.
            row.error_message = output.error[:2000]
        await ds2.commit()

    _logger.info(
        "transcribe_audio_session %s → %s (cost=%s)",
        session_id,
        "ready" if output.error is None else "failed",
        output.cost_usd,
    )
    return {
        "session_id": session_id,
        "status": "ready" if output.error is None else "failed",
        "cost_usd": str(output.cost_usd),
        "error": output.error,
    }


async def _persist_failure(
    *,
    session_maker: async_sessionmaker,  # type: ignore[type-arg]
    session_uuid: UUID,
    error: str,
) -> dict[str, Any]:
    """Записать ``status=failed`` + error_message; вернуть summary."""
    async with session_maker() as ds:
        row = await ds.get(AudioSession, session_uuid)
        if row is not None:
            row.status = "failed"
            row.error_message = error[:2000]
            await ds.commit()
    return {
        "session_id": str(session_uuid),
        "status": "failed",
        "error": error,
    }


__all__ = ["transcribe_audio_session"]
