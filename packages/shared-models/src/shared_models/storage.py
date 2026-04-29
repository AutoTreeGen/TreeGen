"""Object-storage abstraction (Phase 4.11a, ADR-0046).

Один Protocol — три реализации:

* :class:`InMemoryStorage` — для тестов и dev-CI без docker. Никаких
  внешних зависимостей; данные живут в process-local dict.
* :class:`MinIOStorage` — для локального dev (через docker-compose) и
  любого S3-compatible хранилища (включая AWS S3 в проде, если
  понадобится). Реализована через ``boto3``; impport boto3 ленивый —
  если его нет в окружении, instantiation поднимает ImportError с
  понятным сообщением.
* :class:`GCSStorage` — production-default на GCP. Реализована через
  ``google-cloud-storage``; lazy import той же логикой.

Выбор бэкенда — env var ``STORAGE_BACKEND`` ∈ {``minio``, ``gcs``,
``memory``}. Дефолт — ``minio`` (локально), production overrides на
``gcs``. Тесты явно используют :class:`InMemoryStorage`.

Контракт: один blob — один key (строка с произвольными ``/`` для
группировки). API async, чтобы вписаться в FastAPI/arq event loop без
блокировки thread-pool'ом. Synchronous-only клиенты (boto3, GCS) ходят
через ``anyio.to_thread.run_sync``.

Signed URLs:
    Каждый бэкенд возвращает временный download URL. Для S3-compatible
    это presigned-URL; для GCS — signed URL v4; для in-memory — fake
    HTTP-style URL вида ``memory://<key>?token=<sig>`` (тесты не
    скачивают реально, только проверяют формат + expiry).

GDPR / privacy:
    * Все бэкенды должны соблюдать TTL ``object_ttl_seconds`` —
      object-lifecycle policy на bucket'е (S3/GCS-side) либо явный delete
      из background sweep'а. Application код ожидает что blob исчезнет
      после TTL без ручного вмешательства.
    * Никаких логов с key в DEBUG/INFO — только в ERROR с context
      (см. ``dna-service.services.storage`` как референс).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hmac
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Final, Protocol

import anyio

_LOG: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SignedUrl:
    """URL с временной валидностью на скачивание blob'а.

    Attributes:
        url: Полный URL (presigned / signed). Любой HTTP-клиент может
            взять его и сделать ``GET``.
        expires_at: UTC-момент истечения. Caller должен передать его
            пользователю (например, в email), чтобы тот понимал когда
            ссылка перестанет работать.
    """

    url: str
    expires_at: dt.datetime


class ObjectStorage(Protocol):
    """Async interface для object-storage (S3 / GCS / in-memory)."""

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Записать blob по ``key``. Перезапись допускается."""

    async def get(self, key: str) -> bytes:
        """Прочитать blob; raises ``FileNotFoundError`` если ``key`` не существует."""

    async def delete(self, key: str) -> None:
        """Удалить blob. Idempotent — no-op если уже нет."""

    async def exists(self, key: str) -> bool:
        """True если blob под ``key`` существует."""

    async def signed_download_url(
        self,
        key: str,
        *,
        expires_in_seconds: int,
    ) -> SignedUrl:
        """Сгенерировать temporary download URL.

        Args:
            key: Blob key. Должен существовать (caller проверяет; для
                несуществующего S3 всё равно вернёт URL, который выдаст 404).
            expires_in_seconds: Лимит жизни URL'а; реализация может
                приклемпить к собственному max'у (напр. AWS — 7 дней).

        Returns:
            :class:`SignedUrl` с full URL и timestamp истечения.
        """


# ---------------------------------------------------------------------------
# In-memory backend (для тестов и dev-без-docker)
# ---------------------------------------------------------------------------


class InMemoryStorage:
    """Хранилище-в-памяти. Thread-safe (подходит для multi-worker tests)."""

    def __init__(self, *, signing_key: bytes | None = None) -> None:
        self._blobs: dict[str, bytes] = {}
        self._lock = threading.Lock()
        # Signing key для fake-presigned URL'ов. None — генерируем ad-hoc.
        # В тестах удобно фиксировать seed для детерминизма.
        self._signing_key: Final = signing_key or os.urandom(32)

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        # content_type игнорируется — InMemory не моделирует metadata.
        _ = content_type
        with self._lock:
            self._blobs[key] = bytes(data)

    async def get(self, key: str) -> bytes:
        with self._lock:
            blob = self._blobs.get(key)
        if blob is None:
            msg = f"Object not found: {key}"
            raise FileNotFoundError(msg)
        return blob

    async def delete(self, key: str) -> None:
        with self._lock:
            self._blobs.pop(key, None)

    async def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._blobs

    async def signed_download_url(
        self,
        key: str,
        *,
        expires_in_seconds: int,
    ) -> SignedUrl:
        # Fake-presigned URL: ``memory://<key>?expires=<unix>&sig=<hmac>``.
        # Полезно тестам — формат стабильный, expires-проверяется на
        # отдельном in-memory download-эндпоинте если потребуется.
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in_seconds)
        expires_unix = int(expires_at.timestamp())
        msg = f"{key}|{expires_unix}".encode()
        sig = hmac.new(self._signing_key, msg, sha256).hexdigest()[:32]
        url = f"memory://{key}?expires={expires_unix}&sig={sig}"
        return SignedUrl(url=url, expires_at=expires_at)


# ---------------------------------------------------------------------------
# MinIO / S3-compatible backend
# ---------------------------------------------------------------------------


class MinIOStorage:
    """S3-compatible backend через ``boto3``. Подходит для MinIO и AWS S3.

    Lazy-import boto3: если пакет не установлен, ``__init__`` поднимет
    ``ImportError`` с указанием extras'а (``shared-models[storage-minio]``).
    Это позволяет дев-окружениям без MinIO не тащить тяжёлый ботокор.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        secure: bool = False,
    ) -> None:
        try:
            import boto3  # noqa: PLC0415  — lazy import (extras)
            from botocore.client import Config  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — env-зависимая ветка
            msg = (
                "MinIOStorage requires boto3. Install via "
                "`uv pip install 'shared-models[storage-minio]'` or add "
                "boto3 to your service's dependencies."
            )
            raise ImportError(msg) from exc

        self._bucket: Final = bucket
        # `s3v4` — единственная sig version, которую MinIO 2024+ принимает
        # для presigned-URL'ов. AWS S3 тоже поддерживает.
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            use_ssl=secure,
            config=Config(signature_version="s3v4"),
        )

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        )

    async def get(self, key: str) -> bytes:
        try:
            response = await anyio.to_thread.run_sync(
                lambda: self._client.get_object(Bucket=self._bucket, Key=key)
            )
        except self._client.exceptions.NoSuchKey as exc:
            msg = f"Object not found: {key}"
            raise FileNotFoundError(msg) from exc
        body = response["Body"]
        try:
            data: bytes = await anyio.to_thread.run_sync(body.read)
        finally:
            body.close()
        return data

    async def delete(self, key: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.delete_object(Bucket=self._bucket, Key=key)
        )

    async def exists(self, key: str) -> bool:
        try:
            await anyio.to_thread.run_sync(
                lambda: self._client.head_object(Bucket=self._bucket, Key=key)
            )
        except Exception:
            # boto3 raises ClientError("404") вариативно по версиям; широкий
            # except — единственный надёжный способ.
            return False
        return True

    async def signed_download_url(
        self,
        key: str,
        *,
        expires_in_seconds: int,
    ) -> SignedUrl:
        url = await anyio.to_thread.run_sync(
            lambda: self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in_seconds,
            )
        )
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in_seconds)
        return SignedUrl(url=str(url), expires_at=expires_at)


# ---------------------------------------------------------------------------
# Google Cloud Storage backend
# ---------------------------------------------------------------------------


class GCSStorage:
    """GCS backend через ``google-cloud-storage``. Production-default на GCP.

    Lazy-import google-cloud-storage: ImportError с указанием extras'а
    (``shared-models[storage-gcs]``) если пакет не установлен.

    Аутентификация: Application Default Credentials. На GCE/GKE/Cloud Run
    автоматически подхватывается metadata-server'ом; локально можно через
    ``gcloud auth application-default login``.
    """

    def __init__(self, *, bucket: str, project: str | None = None) -> None:
        try:
            from google.cloud import storage  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            msg = (
                "GCSStorage requires google-cloud-storage. Install via "
                "`uv pip install 'shared-models[storage-gcs]'`."
            )
            raise ImportError(msg) from exc

        self._client = storage.Client(project=project)
        self._bucket = self._client.bucket(bucket)

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        blob = self._bucket.blob(key)
        await anyio.to_thread.run_sync(
            lambda: blob.upload_from_string(data, content_type=content_type)
        )

    async def get(self, key: str) -> bytes:
        blob = self._bucket.blob(key)

        def _download() -> bytes:
            if not blob.exists():
                msg = f"Object not found: {key}"
                raise FileNotFoundError(msg)
            return bytes(blob.download_as_bytes())

        result: bytes = await anyio.to_thread.run_sync(_download)
        return result

    async def delete(self, key: str) -> None:
        blob = self._bucket.blob(key)

        def _delete() -> None:
            # google.api_core.exceptions.NotFound — широкий suppress для
            # idempotent delete.
            with contextlib.suppress(Exception):
                blob.delete()

        await anyio.to_thread.run_sync(_delete)

    async def exists(self, key: str) -> bool:
        blob = self._bucket.blob(key)
        return bool(await anyio.to_thread.run_sync(blob.exists))

    async def signed_download_url(
        self,
        key: str,
        *,
        expires_in_seconds: int,
    ) -> SignedUrl:
        blob = self._bucket.blob(key)
        url = await anyio.to_thread.run_sync(
            lambda: blob.generate_signed_url(
                version="v4",
                expiration=dt.timedelta(seconds=expires_in_seconds),
                method="GET",
            )
        )
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in_seconds)
        return SignedUrl(url=str(url), expires_at=expires_at)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_BACKEND_MEMORY: Final = "memory"
_BACKEND_MINIO: Final = "minio"
_BACKEND_GCS: Final = "gcs"
_VALID_BACKENDS: Final = frozenset({_BACKEND_MEMORY, _BACKEND_MINIO, _BACKEND_GCS})


def build_storage_from_env(prefix: str = "STORAGE") -> ObjectStorage:
    """Сконструировать backend по переменным окружения.

    Читает:

    * ``{prefix}_BACKEND`` — ``minio`` (default) | ``gcs`` | ``memory``.
    * для MinIO: ``{prefix}_ENDPOINT_URL``, ``{prefix}_ACCESS_KEY``,
      ``{prefix}_SECRET_KEY``, ``{prefix}_BUCKET``,
      опц. ``{prefix}_REGION`` (default ``us-east-1``),
      опц. ``{prefix}_SECURE`` (``"true"``/``"false"``).
    * для GCS: ``{prefix}_BUCKET``, опц. ``{prefix}_PROJECT``.

    Валидация на uppercase — env var имена case-sensitive.
    """
    backend = os.environ.get(f"{prefix}_BACKEND", _BACKEND_MINIO).lower()
    if backend not in _VALID_BACKENDS:
        msg = f"Unknown {prefix}_BACKEND={backend!r}. Expected one of: {sorted(_VALID_BACKENDS)}"
        raise ValueError(msg)

    if backend == _BACKEND_MEMORY:
        return InMemoryStorage()

    bucket = _require_env(f"{prefix}_BUCKET")
    if backend == _BACKEND_GCS:
        return GCSStorage(
            bucket=bucket,
            project=os.environ.get(f"{prefix}_PROJECT") or None,
        )

    # MinIO / S3-compatible
    secure = os.environ.get(f"{prefix}_SECURE", "false").lower() in ("1", "true", "yes")
    return MinIOStorage(
        endpoint_url=_require_env(f"{prefix}_ENDPOINT_URL"),
        access_key=_require_env(f"{prefix}_ACCESS_KEY"),
        secret_key=_require_env(f"{prefix}_SECRET_KEY"),
        bucket=bucket,
        region=os.environ.get(f"{prefix}_REGION", "us-east-1"),
        secure=secure,
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        msg = f"{name} env var is required for the selected STORAGE_BACKEND"
        raise ValueError(msg)
    return value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def gdpr_export_key(*, user_id: uuid.UUID, request_id: uuid.UUID) -> str:
    """Каноническая раскладка key'а для GDPR exports.

    Формат: ``gdpr-exports/{user_id}/{request_id}.zip``. Конвенция —
    user_id префиксом, чтобы lifecycle-policy на bucket'е могла
    fine-grained менять retention per-user (default 30 дней).
    """
    return f"gdpr-exports/{user_id}/{request_id}.zip"


__all__ = [
    "GCSStorage",
    "InMemoryStorage",
    "MinIOStorage",
    "ObjectStorage",
    "SignedUrl",
    "build_storage_from_env",
    "gdpr_export_key",
]


# Suppress F401 for Any — declared for callers that subclass-type the protocol.
_ = Any
