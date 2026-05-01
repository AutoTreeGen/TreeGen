"""Audio sessions API (Phase 10.9a / ADR-0064 §3.3).

CRUD над ``audio_sessions`` table:

* ``POST   /trees/{tree_id}/audio-sessions`` — multipart upload (EDITOR).
  Privacy gate: 403 ``consent_required`` если у дерева нет consent'а
  (defence-in-depth поверх UI-disabled-кнопки и DB ``NOT NULL`` constraint
  на ``consent_egress_at``). Файл сохраняется в storage, создаётся row
  со status=``uploaded``, enqueue arq ``transcribe_audio_session``.
* ``GET    /trees/{tree_id}/audio-sessions`` — paginated list (VIEWER).
* ``GET    /audio-sessions/{id}`` — single (VIEWER, manual check).
* ``DELETE /audio-sessions/{id}`` — soft-delete (EDITOR, manual check).

Контракт ролей соответствует ADR-0036: запись/удаление — EDITOR;
чтение — VIEWER. Owner-only — только consent (см. ``audio_consent.py``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Final

from arq.connections import ArqRedis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from shared_models import TreeRole
from shared_models.orm import AudioSession, Tree
from shared_models.storage import ObjectStorage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import RequireUser
from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.queue import get_arq_pool
from parser_service.schemas import (
    AudioSessionListResponse,
    AudioSessionResponse,
)
from parser_service.services.audio_storage import (
    audio_object_key,
    get_audio_storage,
    storage_uri,
)
from parser_service.services.permissions import (
    check_tree_permission,
    require_tree_role,
)

# Имя arq-функции, которое регистрирует worker (см. ``parser_service.worker``).
# Захардкожено как строковая константа — endpoint не импортирует worker,
# чтобы не тащить ai-layer в HTTP-слой (см. note в ``audio_consent.py``).
TRANSCRIBE_AUDIO_SESSION_JOB_NAME = "transcribe_audio_session"

# MIME-типы аудио, которые принимает MediaRecorder в браузере + типичные
# конверторы. Совпадает с ai_layer.clients.whisper._MIME_TO_EXT базовыми
# значениями. Каждое лишнее значение здесь = +1 attack surface (Whisper
# отклонит, но мы сами отвечаем 415 раньше — короче latency, чище логи).
_ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/flac",
    }
)

# Pagination caps — совпадают с конвенцией остальных list-эндпоинтов
# (sources, persons, ...).
_DEFAULT_PER_PAGE: Final[int] = 20
_MAX_PER_PAGE: Final[int] = 100

router = APIRouter()


def _normalize_mime(raw: str | None) -> str:
    """MIME → lowercase + базовый тип (отрезаем codecs-suffix).

    MediaRecorder отдаёт ``audio/webm;codecs=opus`` — для allowlist'а нам
    нужен только base type. Whisper API принимает либо так, либо так.
    """
    if not raw:
        return ""
    return raw.split(";", maxsplit=1)[0].strip().lower()


def _to_response(session: AudioSession) -> AudioSessionResponse:
    """ORM ``AudioSession`` → DTO ``AudioSessionResponse``.

    Ручной маппинг, не ``model_validate``: ``status`` в ORM — ``str``,
    но Pydantic Literal ждёт точное значение. Cast валидирует через
    Pydantic-валидацию автоматически.
    """
    return AudioSessionResponse.model_validate(session)


@router.post(
    "/trees/{tree_id}/audio-sessions",
    response_model=AudioSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Editor-only — upload voice session + enqueue transcription.",
    dependencies=[Depends(require_tree_role(TreeRole.EDITOR))],
)
async def create_audio_session(
    tree_id: uuid.UUID,
    user_id: RequireUser,
    settings: Annotated[Settings, Depends(get_settings)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    storage: Annotated[ObjectStorage, Depends(get_audio_storage)],
    audio: Annotated[UploadFile, File(description="Audio blob (≤ AUDIO_MAX_SIZE_BYTES).")],
    language_hint: Annotated[
        str | None,
        Form(description="ISO-639 язык-подсказка для Whisper (e.g. 'ru', 'en')."),
    ] = None,
) -> AudioSessionResponse:
    """Принять multipart audio-blob + создать ``AudioSession(status=uploaded)``
    + enqueue arq worker для транскрипции.

    Защита в три слоя (ADR-0064 §Риски):

    1. **Consent gate** — ``Tree.audio_consent_egress_at IS NULL`` →
       403 ``consent_required``. Snapshot consent'а пишется в
       ``AudioSession.consent_egress_at`` для immutable provenance.
    2. **STT availability** — нет ``OPENAI_API_KEY`` и не ``AI_DRY_RUN``
       → 503 ``stt_unavailable``. Нечего ставить в очередь, если worker
       всё равно вернёт ``failed``; fail-fast лучше.
    3. **Validation** — MIME-allowlist (415), size cap (413).

    После всех проверок: blob → storage, ORM-row → DB, job → arq.
    UploadFile **не** сериализуется в Redis (`bytes` слишком тяжёлые
    для очереди); worker читает по ``storage_uri`` из ORM.
    """
    # ---- 1. STT availability (fail-fast до чтения файла) -------------------
    if not settings.openai_api_key and not settings.ai_dry_run:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "stt_unavailable",
                "message": (
                    "Speech-to-text provider is not configured (OPENAI_API_KEY "
                    "missing and AI_DRY_RUN=false)."
                ),
            },
        )

    # ---- 2. Consent gate ---------------------------------------------------
    tree = await db_session.get(Tree, tree_id)
    if tree is None:  # pragma: no cover — gate уже проверил, double-safety
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {tree_id} not found",
        )
    if tree.audio_consent_egress_at is None or tree.audio_consent_egress_provider is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "consent_required",
                "tree_id": str(tree_id),
                "message": (
                    "Voice egress consent has not been granted for this tree. "
                    "POST /trees/{tree_id}/audio-consent first."
                ),
            },
        )

    # ---- 3. MIME validation (415) -----------------------------------------
    mime_type = _normalize_mime(audio.content_type)
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported audio MIME type {audio.content_type!r}; "
                f"accepted: {sorted(_ALLOWED_MIME_TYPES)}."
            ),
        )

    # ---- 4. Read blob + size check (413) ----------------------------------
    raw_bytes = await audio.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Empty audio upload.",
        )
    if len(raw_bytes) > settings.audio_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Audio upload exceeds limit: {len(raw_bytes)} bytes > "
                f"{settings.audio_max_size_bytes}."
            ),
        )

    # ---- 5. Persist + storage + enqueue -----------------------------------
    # ID генерируем на app-стороне до записи в storage — нам нужен key
    # ``sessions/{id}.{ext}`` ДО ORM-row INSERT'а (иначе пришлось бы
    # делать flush, читать id, потом put_object с update — лишний trip).
    session_id = uuid.uuid4()
    key = audio_object_key(session_id, mime_type)
    uri = storage_uri(session_id, mime_type, bucket=settings.audio_storage_bucket)

    await storage.put(key, raw_bytes, content_type=mime_type)

    audio_session = AudioSession(
        id=session_id,
        tree_id=tree_id,
        owner_user_id=user_id,
        storage_uri=uri,
        mime_type=mime_type,
        size_bytes=len(raw_bytes),
        status="uploaded",
        language=language_hint,
        # Snapshot consent: NOT NULL на DB-уровне; revoke consent'а потом
        # не «откатит» privacy для уже отправленных сессий (immutable
        # provenance, ADR-0064 §B1).
        consent_egress_at=tree.audio_consent_egress_at,
        consent_egress_provider=tree.audio_consent_egress_provider,
        provenance={"upload_request_user_id": str(user_id)},
    )
    db_session.add(audio_session)
    await db_session.flush()
    await db_session.refresh(audio_session)

    await pool.enqueue_job(
        TRANSCRIBE_AUDIO_SESSION_JOB_NAME,
        str(session_id),
        _job_id=f"transcribe_audio_session:{session_id}",
    )

    return _to_response(audio_session)


@router.get(
    "/trees/{tree_id}/audio-sessions",
    response_model=AudioSessionListResponse,
    summary="Viewer-only — paginated list of audio sessions in tree.",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def list_audio_sessions(
    tree_id: uuid.UUID,
    db_session: Annotated[AsyncSession, Depends(get_session)],
    page: int = 1,
    per_page: int = _DEFAULT_PER_PAGE,
) -> AudioSessionListResponse:
    """Сессии дерева, отсортированы по ``created_at DESC``.

    Soft-deleted сессии включены — UI рисует tombstone-стиль и фильтрует
    сам, если нужно скрыть. Это важно для аудита: после revoke consent'а
    хочется видеть, что сессии действительно ушли в erasure.
    """
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="page must be >= 1",
        )
    if per_page < 1 or per_page > _MAX_PER_PAGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"per_page must be 1..{_MAX_PER_PAGE}",
        )

    total = await db_session.scalar(
        select(func.count(AudioSession.id)).where(AudioSession.tree_id == tree_id)
    )

    rows = await db_session.execute(
        select(AudioSession)
        .where(AudioSession.tree_id == tree_id)
        .order_by(AudioSession.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    items = [_to_response(row) for row in rows.scalars().all()]

    return AudioSessionListResponse(
        tree_id=tree_id,
        total=int(total or 0),
        page=page,
        per_page=per_page,
        items=items,
    )


@router.get(
    "/audio-sessions/{session_id}",
    response_model=AudioSessionResponse,
    summary="Viewer-only — single audio session detail.",
)
async def get_audio_session(
    session_id: uuid.UUID,
    user_id: RequireUser,
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> AudioSessionResponse:
    """Один ``AudioSession`` row.

    ``tree_id`` не в path → manual permission check (gate factory не
    подходит). 404 для cross-tree user'а (privacy: не различаем «нет
    сессии» от «нет доступа к дереву»).
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
        required=TreeRole.VIEWER,
    )
    if not has_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AudioSession {session_id} not found",
        )
    return _to_response(row)


@router.delete(
    "/audio-sessions/{session_id}",
    response_model=AudioSessionResponse,
    summary="Editor-only — soft-delete an audio session.",
)
async def soft_delete_audio_session(
    session_id: uuid.UUID,
    user_id: RequireUser,
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> AudioSessionResponse:
    """Soft-delete: ``deleted_at = now()``. Blob НЕ удаляется (это —
    erasure-pipeline через ``DELETE /audio-consent``).

    Идемпотентно: повторный DELETE на уже-удалённой сессии возвращает
    ту же row без обновления timestamp'а (provenance остаётся точной).
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
        required=TreeRole.EDITOR,
    )
    if not has_role:
        # Ту же 404 что и в GET — не утечь cross-tree существование.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AudioSession {session_id} not found",
        )
    if row.deleted_at is None:
        row.deleted_at = dt.datetime.now(dt.UTC)
        await db_session.flush()
        # Refresh нужен чтобы ``updated_at`` (server-side default ON UPDATE)
        # подхватился; без него ``model_validate`` ниже триггерит async lazy
        # load в синхронном контексте → MissingGreenlet.
        await db_session.refresh(row)
    return _to_response(row)


__all__ = ["TRANSCRIBE_AUDIO_SESSION_JOB_NAME", "router"]
