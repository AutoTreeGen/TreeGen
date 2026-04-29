"""One-time link-токены для opt-in account linking (Phase 14.0).

Хранение — Redis. Ключ ``tg:link:{token}`` → JSON ``{tg_chat_id,
tg_user_id, issued_at}``. TTL — ``settings.link_ttl_seconds``.
Single-use: consume = ``GETDEL`` (атомарно), повторный consume
возвращает None.

Token format — 32 байта random, `secrets.token_urlsafe(32)`,
получается ~43 base64url-символа.

См. ADR-0040 §«Account linking flow».
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from dataclasses import dataclass

from redis.asyncio import Redis

_KEY_PREFIX = "tg:link:"


@dataclass(frozen=True)
class LinkTokenPayload:
    """Что мы храним за токеном."""

    tg_chat_id: int
    tg_user_id: int
    issued_at: dt.datetime


class LinkTokenStore:
    """Mint / consume одноразовых link-токенов в Redis."""

    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        """Конструктор.

        Args:
            redis: Async Redis-клиент.
            ttl_seconds: TTL токена в секундах. Должен быть > 0.
        """
        if ttl_seconds <= 0:
            msg = "ttl_seconds must be positive"
            raise ValueError(msg)
        self._redis = redis
        self._ttl = ttl_seconds

    async def mint(self, *, tg_chat_id: int, tg_user_id: int) -> str:
        """Сгенерировать токен и положить в Redis.

        Returns:
            Token строка (~43 chars URL-safe base64).
        """
        token = secrets.token_urlsafe(32)
        payload = {
            "tg_chat_id": tg_chat_id,
            "tg_user_id": tg_user_id,
            "issued_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        await self._redis.set(
            _KEY_PREFIX + token,
            json.dumps(payload),
            ex=self._ttl,
        )
        return token

    async def consume(self, token: str) -> LinkTokenPayload | None:
        """Атомарно прочитать и удалить токен.

        Returns:
            ``LinkTokenPayload`` если токен валиден и ещё не consumed,
            иначе ``None`` (expired / replay / invalid).
        """
        raw = await self._redis.getdel(_KEY_PREFIX + token)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return LinkTokenPayload(
            tg_chat_id=int(data["tg_chat_id"]),
            tg_user_id=int(data["tg_user_id"]),
            issued_at=dt.datetime.fromisoformat(data["issued_at"]),
        )
