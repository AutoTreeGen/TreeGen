"""At-rest encryption для FamilySearch refresh-токенов в Redis.

**TODO (follow-up).** Этот модуль дублирует логику
``parser_service.fs_oauth.TokenStorage`` (Phase 5.1 / ADR-0027); планируется
извлечь общий примитив в ``shared_models.security`` (или новый
``shared_models.fs_oauth``) и заменить оба места одним переиспользуемым
кодом. Текущая дубликация осознанная — Phase 9.0 scaffold не должен
ломать parser-service, а изменение публичного API ``shared-models``
выходит за scope этого PR (см. ADR-0055 §«Open questions»).

Шифрование — Fernet (cryptography). Ключ — urlsafe-base64, 32 bytes,
из ENV ``ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY``. Ротация ключа в Phase 9.0
не предусмотрена; будет вместе с extraction в shared-models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis_asyncio
from cryptography.fernet import Fernet, InvalidToken
from familysearch_client import Token


class TokenCryptoError(RuntimeError):
    """Криптографическая ошибка (битый ключ / битый шифротекст)."""


@dataclass(frozen=True, slots=True)
class StoredToken:
    """Десериализованный токен из Redis (JSON под Fernet-шифротекстом)."""

    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str | None

    @classmethod
    def from_token(cls, token: Token) -> StoredToken:
        return cls(
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_in=token.expires_in,
            scope=token.scope,
        )


class TokenStorage:
    """Шифрует FS-токены и кладёт их в Redis с TTL.

    Ключ Redis — ``fs:token:{user_id}``. TTL = ``token.expires_in``,
    поэтому storage сам очищает протухшие записи без cron-job'а.
    """

    REDIS_KEY_PREFIX = "fs:token"

    def __init__(self, *, fernet_key: str) -> None:
        if not fernet_key:
            msg = "fernet_key is empty; refusing to construct TokenStorage."
            raise TokenCryptoError(msg)
        try:
            self._fernet = Fernet(fernet_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            msg = f"invalid Fernet key: {exc}"
            raise TokenCryptoError(msg) from exc

    @classmethod
    def make_redis_key(cls, user_id: str) -> str:
        return f"{cls.REDIS_KEY_PREFIX}:{user_id}"

    def _encrypt(self, payload: dict[str, Any]) -> bytes:
        ciphertext: bytes = self._fernet.encrypt(json.dumps(payload).encode("utf-8"))
        return ciphertext

    def _decrypt(self, ciphertext: str | bytes) -> dict[str, Any]:
        if isinstance(ciphertext, str):
            ciphertext = ciphertext.encode("ascii")
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            msg = "Fernet token is invalid (key rotated or ciphertext tampered)."
            raise TokenCryptoError(msg) from exc
        result: dict[str, Any] = json.loads(plaintext.decode("utf-8"))
        return result

    async def save(
        self,
        redis: redis_asyncio.Redis,
        *,
        user_id: str,
        token: Token,
    ) -> None:
        """Сохранить шифрованный токен с TTL = ``token.expires_in``.

        Если ``expires_in == 0`` — TTL не выставляется (никогда не
        протухает; в проде такого не должно быть, но контракт мягкий).
        """
        stored = StoredToken.from_token(token)
        ciphertext = self._encrypt(
            {
                "access_token": stored.access_token,
                "refresh_token": stored.refresh_token,
                "expires_in": stored.expires_in,
                "scope": stored.scope,
            }
        )
        ttl = max(int(token.expires_in), 0) or None
        await redis.set(self.make_redis_key(user_id), ciphertext, ex=ttl)

    async def load(
        self,
        redis: redis_asyncio.Redis,
        *,
        user_id: str,
    ) -> StoredToken | None:
        """Загрузить и расшифровать токен; ``None`` если ключа нет."""
        raw = await redis.get(self.make_redis_key(user_id))
        if raw is None:
            return None
        payload = self._decrypt(raw)
        return StoredToken(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_in=int(payload.get("expires_in", 0)),
            scope=payload.get("scope"),
        )

    async def delete(
        self,
        redis: redis_asyncio.Redis,
        *,
        user_id: str,
    ) -> None:
        await redis.delete(self.make_redis_key(user_id))
