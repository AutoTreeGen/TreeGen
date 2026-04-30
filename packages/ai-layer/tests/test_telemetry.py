"""Тесты ``log_ai_usage`` (Phase 10.1).

Использует ``fakeredis.aioredis.FakeRedis`` (in-memory Redis-stub) —
сетевые вызовы не нужны. Сценарии:

- happy-path: запись попадает в LIST, поля сериализуются как ожидалось,
  expire выставлен;
- ``user_id=None``: сериализуется как ``"user_id": null``;
- request_id auto-generation: при отсутствии параметра генерируется UUID4;
- failing redis: write-error не валит вызов use-case'а (telemetry — fire-and-forget).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest
from ai_layer.telemetry import LOG_KEY, RETENTION_SECONDS, log_ai_usage


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    """Изолированный in-memory Redis на каждый тест."""
    server = fakeredis.aioredis.FakeServer()
    return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)


@pytest.mark.asyncio
async def test_log_ai_usage_writes_record_to_redis(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    user_id = uuid4()
    request_id = uuid4()
    rid = await log_ai_usage(
        redis=fake_redis,
        use_case="explain_hypothesis",
        model="claude-sonnet-4-6",
        input_tokens=2_500,
        output_tokens=300,
        cost_usd=0.0120,
        user_id=user_id,
        request_id=request_id,
        extra={"locale": "en"},
    )
    assert rid == str(request_id)

    items = await fake_redis.lrange(LOG_KEY, 0, -1)
    assert len(items) == 1
    record = json.loads(items[0])
    assert record["use_case"] == "explain_hypothesis"
    assert record["model"] == "claude-sonnet-4-6"
    assert record["input_tokens"] == 2_500
    assert record["output_tokens"] == 300
    assert record["cost_usd"] == 0.0120
    assert record["user_id"] == str(user_id)
    assert record["request_id"] == str(request_id)
    assert record["extra"] == {"locale": "en"}
    assert "ts" in record


@pytest.mark.asyncio
async def test_log_ai_usage_sets_expire(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """LIST получает TTL 30 дней (≈ 2_592_000 сек)."""
    await log_ai_usage(
        redis=fake_redis,
        use_case="explain_hypothesis",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0001,
    )
    ttl = await fake_redis.ttl(LOG_KEY)
    # ttl должен быть положительным и не превышать RETENTION_SECONDS.
    assert 0 < ttl <= RETENTION_SECONDS
    # Точное значение в момент сразу после EXPIRE.
    assert ttl == RETENTION_SECONDS


@pytest.mark.asyncio
async def test_log_ai_usage_without_user_id_serializes_null(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    await log_ai_usage(
        redis=fake_redis,
        use_case="explain_hypothesis",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0001,
    )
    items = await fake_redis.lrange(LOG_KEY, 0, -1)
    record = json.loads(items[0])
    assert record["user_id"] is None


@pytest.mark.asyncio
async def test_log_ai_usage_generates_request_id_when_missing(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    rid = await log_ai_usage(
        redis=fake_redis,
        use_case="explain_hypothesis",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0001,
    )
    # Должно быть валидным UUID4.
    parsed = UUID(rid)
    assert parsed.version == 4


@pytest.mark.asyncio
async def test_log_ai_usage_swallows_redis_failure() -> None:
    """Telemetry — fire-and-forget: если Redis недоступен, use-case не падает."""

    err = "redis is down"

    class BrokenRedis:
        async def lpush(self, *_: Any, **__: Any) -> None:
            raise ConnectionError(err)

        async def expire(self, *_: Any, **__: Any) -> None:
            raise ConnectionError(err)

    rid = await log_ai_usage(
        redis=BrokenRedis(),
        use_case="explain_hypothesis",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0001,
    )
    # Возвращает request_id даже при сбое записи.
    UUID(rid)


@pytest.mark.asyncio
async def test_log_ai_usage_appends_multiple_records(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Несколько вызовов → несколько записей в LIST."""
    for i in range(3):
        await log_ai_usage(
            redis=fake_redis,
            use_case="explain_hypothesis",
            model="claude-sonnet-4-6",
            input_tokens=10 * (i + 1),
            output_tokens=5,
            cost_usd=0.0001 * (i + 1),
        )
    items = await fake_redis.lrange(LOG_KEY, 0, -1)
    assert len(items) == 3
    # LPUSH → новейшая запись первой.
    parsed = [json.loads(item) for item in items]
    assert [r["input_tokens"] for r in parsed] == [30, 20, 10]
