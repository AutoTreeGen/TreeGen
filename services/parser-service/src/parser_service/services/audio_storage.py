"""Audio-blob storage helpers (Phase 10.9a / ADR-0064).

Тонкая обёртка вокруг ``shared_models.storage`` с двумя задачами:

1. Сконструировать backend под ``audio_storage_bucket`` из parser-service
   settings (отдельный bucket от ``STORAGE_BUCKET``, чтобы lifecycle-policy
   был агрессивнее: ADR-0064 §F1 — после успешной транскрипции blob
   должен исчезнуть).
2. Унифицировать key-naming: ``sessions/{session_id}.{ext}`` — short flat
   layout, легко отображается в S3-консолях; нет per-tree pre-fixа,
   потому что privacy gate уже на app-слое (ADR-0064 §B1).

Backend выбирается через ``STORAGE_BACKEND`` (тот же что использует GDPR
exports / общая инфраструктура) — это даёт единое поведение «memory в
тестах, minio локально, gcs в проде». Переменные окружения для подключения
к backend-у тоже общие (``STORAGE_ENDPOINT_URL``, ``STORAGE_ACCESS_KEY``,
...) — отличается только bucket.
"""

from __future__ import annotations

import os
import uuid
from typing import Annotated, Final

from fastapi import Depends
from shared_models.storage import (
    GCSStorage,
    InMemoryStorage,
    MinIOStorage,
    ObjectStorage,
)

from parser_service.config import Settings, get_settings

_BACKEND_MEMORY: Final = "memory"
_BACKEND_MINIO: Final = "minio"
_BACKEND_GCS: Final = "gcs"
_VALID_BACKENDS: Final = frozenset({_BACKEND_MEMORY, _BACKEND_MINIO, _BACKEND_GCS})

# MIME-тип → расширение для key-naming. Не для inference Whisper'а
# (там собственный маппинг в ai-layer), а для S3/GCS-консольной читаемости.
# Список совпадает с ``ai_layer.clients.whisper._MIME_TO_EXT`` базовыми
# значениями; не дублируем, потому что несколько aliases (audio/wave →
# wav и т.п.) не критичны для самого storage.
_MIME_TO_EXT: Final[dict[str, str]] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/flac": "flac",
}

# Module-singleton: один backend на процесс. Тесты не зависят от этого
# (override через ``app.dependency_overrides[get_audio_storage]``);
# production-runtime избегает повторной инициализации boto3/GCS-client'ов.
_storage_singleton: ObjectStorage | None = None


def _build_audio_storage(settings: Settings) -> ObjectStorage:
    """Сконструировать ``ObjectStorage`` для voice-блобов.

    Backend читается из ``STORAGE_BACKEND`` (тот же глобальный flag,
    что у GDPR exports — конвенция ADR-0046 §«Storage prefix»). Bucket
    берётся из ``settings.audio_storage_bucket``, не из ``STORAGE_BUCKET``.

    Raises:
        ValueError: Неизвестный ``STORAGE_BACKEND`` или отсутствующий
            обязательный env-var (для MinIO/GCS — endpoint/access keys).
    """
    backend = os.environ.get("STORAGE_BACKEND", _BACKEND_MINIO).lower()
    if backend not in _VALID_BACKENDS:
        msg = f"Unknown STORAGE_BACKEND={backend!r}. Expected one of: {sorted(_VALID_BACKENDS)}"
        raise ValueError(msg)

    bucket = settings.audio_storage_bucket

    if backend == _BACKEND_MEMORY:
        return InMemoryStorage()

    if backend == _BACKEND_GCS:
        return GCSStorage(
            bucket=bucket,
            project=os.environ.get("STORAGE_PROJECT") or None,
        )

    # MinIO / S3-compatible — общие env-переменные для всех buckets.
    secure = os.environ.get("STORAGE_SECURE", "false").lower() in ("1", "true", "yes")
    return MinIOStorage(
        endpoint_url=_require_env("STORAGE_ENDPOINT_URL"),
        access_key=_require_env("STORAGE_ACCESS_KEY"),
        secret_key=_require_env("STORAGE_SECRET_KEY"),
        bucket=bucket,
        region=os.environ.get("STORAGE_REGION", "us-east-1"),
        secure=secure,
    )


def _require_env(name: str) -> str:
    """Прочитать обязательную env-переменную или поднять ValueError."""
    value = os.environ.get(name)
    if not value:
        msg = f"{name} env var is required for the selected STORAGE_BACKEND"
        raise ValueError(msg)
    return value


def get_audio_storage(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ObjectStorage:
    """FastAPI-зависимость: ``ObjectStorage`` для аудио-блобов.

    Тесты подменяют через ``app.dependency_overrides[get_audio_storage]``
    (типичный паттерн — :class:`InMemoryStorage`). Production-runtime
    кеширует singleton, чтобы не пересоздавать boto3/GCS-client каждый
    запрос.
    """
    global _storage_singleton  # noqa: PLW0603 — module-singleton по дизайну.
    if _storage_singleton is None:
        _storage_singleton = _build_audio_storage(settings)
    return _storage_singleton


def reset_audio_storage_cache() -> None:
    """Сбросить module-singleton — для тестов и hot-reload'а конфига."""
    global _storage_singleton  # noqa: PLW0603 — см. ``get_audio_storage``.
    _storage_singleton = None


def audio_object_key(session_id: uuid.UUID, mime_type: str) -> str:
    """Каноническая раскладка key'я для blob аудио-сессии.

    Формат: ``sessions/{session_id}.{ext}``. ``ext`` определяется по MIME;
    fallback — ``bin`` (S3-консоль покажет «unknown», но blob всё равно
    читается worker'ом по этому key'ю).
    """
    base = mime_type.split(";", maxsplit=1)[0].strip().lower()
    ext = _MIME_TO_EXT.get(base, "bin")
    return f"sessions/{session_id}.{ext}"


def storage_uri(session_id: uuid.UUID, mime_type: str, *, bucket: str) -> str:
    """Сформировать ``AudioSession.storage_uri`` в формате ``s3://bucket/key``.

    Format одинаков для MinIO и GCS — caller (worker) использует
    ``audio_object_key`` для лукапа, не парсит URI обратно. URI хранится
    как «human-friendly» reference в ORM для аудитов и инспекций.
    """
    return f"s3://{bucket}/{audio_object_key(session_id, mime_type)}"


__all__ = [
    "audio_object_key",
    "get_audio_storage",
    "reset_audio_storage_cache",
    "storage_uri",
]
