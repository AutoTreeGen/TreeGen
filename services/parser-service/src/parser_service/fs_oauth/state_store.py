"""Хранилище OAuth state-token'ов в Redis (Phase 5.1, ADR-0027 §CSRF).

Между ``GET /familysearch/oauth/start`` и ``GET /familysearch/oauth/callback``
надо сохранить:

* ``code_verifier`` — секрет, без которого token-exchange упадёт
  (FS не отдаст токен).
* ``user_id`` — кому привязать токен после callback'а. Без этого мы
  не знаем, в чью строку ``users.fs_token_encrypted`` писать.
* ``redirect_uri`` — RFC 6749 требует совпадения, фиксируем,
  чтобы не угнать сравнение с конфигом наживо.

Положить это в БД — overhead (transient row + GC). Cookie — выдать
пользователю секрет = пробить наш собственный CSRF. Используем Redis
с TTL: ключ ``fs:oauth:state:<state>``, value — JSON-payload.
TTL = 10 минут (FS callback приходит сильно быстрее).

Метод :func:`consume_state` — atomic «прочитать и удалить» через
``GETDEL`` (Redis 6.2+), чтобы один state нельзя было использовать
дважды (replay-protection).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol


class _RedisLike(Protocol):
    """Минимальный интерфейс Redis-клиента, который нам нужен.

    Совместим с ``redis.asyncio.Redis`` и ``arq.connections.ArqRedis``
    (subclass), а также с ``fakeredis.aioredis.FakeRedis`` для тестов.
    """

    async def setex(self, name: str, time: int, value: str) -> Any: ...
    async def getdel(self, name: str) -> Any: ...
    async def delete(self, *names: str) -> Any: ...


_KEY_PREFIX = "fs:oauth:state:"


@dataclass(frozen=True, kw_only=True, slots=True)
class OAuthStateRecord:
    """Что хранится между start_flow и callback'ом FS.

    Attributes:
        state: Сам CSRF-state (тот же, что в URL).
        code_verifier: PKCE-секрет (RFC 7636).
        user_id: К какому user'у привязать выданный токен.
        redirect_uri: RFC 6749 § 4.1.3 — должен совпадать в start и token-exchange.
        scope: Какой scope попросили (для логов/диагностики).
    """

    state: str
    code_verifier: str
    user_id: uuid.UUID
    redirect_uri: str
    scope: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """JSON-friendly представление для Redis."""
        return {
            "state": self.state,
            "code_verifier": self.code_verifier,
            "user_id": str(self.user_id),
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OAuthStateRecord:
        """Восстановить из Redis JSON. KeyError → нелегитимная запись."""
        return cls(
            state=str(payload["state"]),
            code_verifier=str(payload["code_verifier"]),
            user_id=uuid.UUID(str(payload["user_id"])),
            redirect_uri=str(payload["redirect_uri"]),
            scope=payload.get("scope"),
        )


def _key(state: str) -> str:
    """Полный Redis-ключ для state-token'а.

    Префикс отделяет «наши» ключи от arq job-stream'ов и job-events
    pubsub-канала. Ключ — case-sensitive base64url state.
    """
    return f"{_KEY_PREFIX}{state}"


async def save_state(redis: _RedisLike, record: OAuthStateRecord, *, ttl_seconds: int) -> None:
    """Атомарно положить state в Redis с TTL.

    Используем ``SETEX`` (а не ``SET ... EX``) — синтаксис проще, любой
    redis-py клиент это умеет (включая fakeredis).
    """
    await redis.setex(_key(record.state), ttl_seconds, json.dumps(record.to_payload()))


async def consume_state(redis: _RedisLike, state: str) -> OAuthStateRecord | None:
    """Прочитать state и **сразу удалить** (replay-protection).

    Returns:
        ``OAuthStateRecord``, если state валиден и ещё жив; ``None``,
        если state уже истёк / был использован / отсутствует.
    """
    raw = await redis.getdel(_key(state))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        # Битый payload — стираем и считаем, что state не было.
        await redis.delete(_key(state))
        return None
    try:
        return OAuthStateRecord.from_payload(payload)
    except (KeyError, ValueError):
        return None
