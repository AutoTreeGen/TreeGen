"""arq job: GDPR-style hard-delete одной ``AudioSession`` (Phase 10.9a).

Триггер: ``DELETE /trees/{id}/audio-consent`` ставит этот job для
каждой неудалённой сессии дерева. Hard-delete blob'а и DB-row'а —
soft-delete не достаточно: ADR-0064 §F1 обещает «после revoke
consent'а аудио уезжает из storage в течение minutes».

Шаги (см. ADR-0049 паттерн):

1. Load row. Если уже удалён — no-op.
2. Delete blob из storage. ``FileNotFoundError`` — не ошибка
   (uploads, которые никогда не достигли storage из-за crash, всё
   равно нужно убрать с DB).
3. Hard-DELETE row (``ondelete=CASCADE`` от ``trees`` — отдельный
   путь; этот worker делает explicit DELETE без удаления tree).
4. Log via standard logger — ``gdpr_erasure_log`` table не существует
   на 10.9a (см. ADR-0049 §«Audit»; в текущей системе erasure'ы
   попадают в ``audit_log`` с ``actor_kind=erasure_request``, но
   audit-trigger AudioSession намеренно отключён — она service-table,
   не domain — поэтому audit-row для voice не имеет смысла).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from shared_models.orm import AudioSession
from shared_models.storage import ObjectStorage
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from parser_service.config import get_settings
from parser_service.database import get_engine
from parser_service.services.audio_storage import (
    audio_object_key,
    get_audio_storage,
)

_logger = logging.getLogger(__name__)


async def erase_audio_session(
    _ctx: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """arq job: hard-delete blob + ``audio_sessions`` row.

    Idempotent: если row уже удалена (например, повторный enqueue после
    revoke consent → новый revoke consent → ещё раз revoke), повторный
    DELETE FROM audio_sessions не находит row, возвращаем no-op summary.

    Args:
        _ctx: arq-контекст; unused (storage инициализируется из env).
        session_id: UUID-string существующей (или уже удалённой)
            ``AudioSession`` row.

    Returns:
        Sterile dict для arq-result'а: ``session_id``, ``deleted`` flag,
        ``blob_deleted`` flag (для admin-аудита частичных failures).
    """
    settings = get_settings()
    storage: ObjectStorage = get_audio_storage(settings)

    session_uuid = UUID(session_id)
    engine = get_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    # Шаг 1: load для получения mime_type (нужен для derive key'я).
    # storage_uri тоже хранится в row, но key — это его суффикс; быстрее
    # пере-derive чем парсить URI.
    async with session_maker() as ds:
        row = await ds.get(AudioSession, session_uuid)
        if row is None:
            _logger.info("erase_audio_session %s: row not found, no-op", session_id)
            return {
                "session_id": session_id,
                "deleted": False,
                "blob_deleted": False,
                "skipped": "not_found",
            }
        mime_type = row.mime_type

    # Шаг 2: delete blob. Idempotent — все три backend'а (memory, MinIO,
    # GCS) трактуют delete несуществующего key'я как no-op.
    key = audio_object_key(session_uuid, mime_type)
    blob_deleted = True
    try:
        await storage.delete(key)
    except FileNotFoundError:
        # Memory backend выкидывает FileNotFoundError на double-delete'ах
        # внутри других реализаций; treat as no-op.
        blob_deleted = False
    except Exception as exc:
        # Сетевой / IAM-fail на S3/GCS — НЕ блокируем DB-delete. Blob
        # «осиротеет», но user'ская привязка к нему уйдёт; admin'у
        # останется bucket-cleanup. ADR-0064 §F1 явно говорит
        # «application делает best-effort, lifecycle-policy — safety net».
        _logger.exception(
            "erase_audio_session %s: storage delete failed (key=%s): %s",
            session_id,
            key,
            exc,
        )
        blob_deleted = False

    # Шаг 3: hard-DELETE row.
    async with session_maker() as ds:
        await ds.execute(delete(AudioSession).where(AudioSession.id == session_uuid))
        await ds.commit()

    _logger.info(
        "erase_audio_session %s: row deleted, blob_deleted=%s",
        session_id,
        blob_deleted,
    )
    return {
        "session_id": session_id,
        "deleted": True,
        "blob_deleted": blob_deleted,
    }


__all__ = ["erase_audio_session"]
