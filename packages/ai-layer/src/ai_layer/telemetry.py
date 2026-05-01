"""Telemetry stub для AI-вызовов (Phase 10.1).

Пишет одну запись на каждый завершённый вызов LLM в Redis-list
``ai_usage:log`` через ``LPUSH`` + ``EXPIRE`` (30 дней). Это **временное**
хранилище: Phase 10.5 (биллинг) переедет на ORM-модель + materialized
агрегаты в Postgres.

Почему Redis сейчас:

- Ноль миграций — Phase 10.1 строго pure-addition (см. ADR-0057).
- Append-only LPUSH дешёвый, expire ограничивает рост.
- Tooling уже есть: parser-service / telegram-bot уже зависят от Redis.

TODO(Phase 10.5): Перенести в таблицу `ai_usage_events` с alembic-миграцией.
TODO(Phase 10.x): PII redaction policy для evidence-полей перед логированием
(сейчас НЕ логируем raw evidence — только метрики).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

LOG_KEY = "ai_usage:log"
RETENTION_SECONDS = 60 * 60 * 24 * 30  # 30 дней

_logger = logging.getLogger(__name__)


class _RedisLike(Protocol):
    """Минимальный async-Redis интерфейс, который нам нужен.

    Совместим с ``redis.asyncio.Redis`` и ``fakeredis.aioredis.FakeRedis``.
    Намеренно не тянем сам ``redis`` как обязательную зависимость
    ai-layer — caller инжектит клиент (FastAPI dependency / arq-worker).
    """

    async def lpush(self, name: str, *values: str) -> Any: ...
    async def expire(self, name: str, time: int) -> Any: ...


async def log_ai_usage(
    *,
    redis: _RedisLike,
    use_case: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | Decimal,
    user_id: UUID | None = None,
    request_id: UUID | None = None,
    audio_duration_sec: float | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Записать одну запись об AI-вызове в Redis-список.

    Args:
        redis: Async Redis-клиент (см. ``_RedisLike``).
        use_case: Логическое имя use-case'а (``"explain_hypothesis"`` и т.п.) —
            нужно для разбивки в аналитике.
        model: Имя модели, которая обслужила вызов (для cost breakdown
            по моделям).
        input_tokens: Сумма prompt-токенов.
        output_tokens: Сумма generated-токенов.
        cost_usd: Расчётная стоимость в USD (см. ``pricing.estimate_cost_usd``
            или ``pricing.estimate_whisper_cost_usd``). Принимаем ``float``
            и :class:`Decimal` (Whisper-флоу — ADR-0064 §3.4 — использует
            Decimal). При сериализации приводим к ``float``: точность
            до 6 знаков допустима для Redis-аналитики (Phase 10.5
            переедет на ORM с native NUMERIC).
        user_id: Опциональный UUID пользователя, инициировавшего вызов.
            ``None`` для системных background-job'ов.
        request_id: Опциональный correlation-ID. Если не передан —
            генерируем новый UUID4.
        audio_duration_sec: Длительность транскрибируемого аудио (Phase
            10.9a). Опциональный — заполняется только для
            ``use_case='transcribe_audio'``. Backward-compatible:
            существующие callsite'ы (Anthropic / Voyage) не передают
            и поле в record просто отсутствует.
        extra: Произвольный JSON-словарь (например, ``{"locale": "ru"}``)
            для use-case-специфичных метрик. Не должен содержать PII —
            проверка ответственности caller'а.

    Returns:
        Сериализованный request_id (для логов / трассировки).
    """
    rid = request_id or uuid4()
    record: dict[str, Any] = {
        "request_id": str(rid),
        "use_case": use_case,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": float(cost_usd),
        "user_id": str(user_id) if user_id is not None else None,
        "ts": datetime.now(UTC).isoformat(),
        "extra": extra or {},
    }
    if audio_duration_sec is not None:
        # Поле добавляется только когда задано — старые consumer'ы Redis-list'а
        # (если читают весь record) не должны видеть `audio_duration_sec: null`
        # на каждой записи. Backward-compatible через optional-key, не optional-value.
        record["audio_duration_sec"] = float(audio_duration_sec)
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    try:
        await redis.lpush(LOG_KEY, payload)
        await redis.expire(LOG_KEY, RETENTION_SECONDS)
    except Exception:
        _logger.warning(
            "ai-layer telemetry write failed; dropping record",
            extra={"use_case": use_case, "request_id": str(rid)},
            exc_info=True,
        )
    return str(rid)
