"""Phase 10.3 — direct unit tests for ``services/ai_normalization.py``.

Покрывают функции, не достижимые через HTTP-слой:

* ``record_normalize_usage`` / ``compute_normalize_budget_report`` —
  Redis day-bucket counters, инвалидация при сбое Redis (fail-open).
* ``build_place_normalizer`` / ``build_name_normalizer`` — конструкторы.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import fakeredis.aioredis
import pytest
from ai_layer import AILayerConfig, BudgetLimits, NameNormalizer, PlaceNormalizer
from parser_service.services.ai_normalization import (
    build_name_normalizer,
    build_place_normalizer,
    compute_normalize_budget_report,
    record_normalize_usage,
)


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    server = fakeredis.aioredis.FakeServer()
    return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)


@pytest.mark.asyncio
async def test_record_and_compute_budget_roundtrip(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    user_id = uuid.uuid4()
    limits = BudgetLimits(max_runs_per_day=10, max_tokens_per_month=100_000)

    # Initially: zero.
    report = await compute_normalize_budget_report(fake_redis, user_id=user_id, limits=limits)
    assert report.runs_in_last_24h == 0
    assert report.tokens_in_last_30d == 0

    # Record three runs.
    for tokens in (200, 300, 400):
        await record_normalize_usage(fake_redis, user_id=user_id, tokens_used=tokens)

    report = await compute_normalize_budget_report(fake_redis, user_id=user_id, limits=limits)
    assert report.runs_in_last_24h == 3
    assert report.tokens_in_last_30d == 900
    assert report.remaining_runs == 7


@pytest.mark.asyncio
async def test_record_and_compute_isolated_per_user(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Counters key'инся per user — usage user'а A не считается у user'а B."""
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    limits = BudgetLimits()
    await record_normalize_usage(fake_redis, user_id=user_a, tokens_used=500)

    report_a = await compute_normalize_budget_report(fake_redis, user_id=user_a, limits=limits)
    report_b = await compute_normalize_budget_report(fake_redis, user_id=user_b, limits=limits)
    assert report_a.runs_in_last_24h == 1
    assert report_b.runs_in_last_24h == 0


@pytest.mark.asyncio
async def test_record_with_zero_tokens_skips_token_counter(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """tokens_used=0 → bumpfor runs only, не пишем в tokens-key (zero-noise)."""
    user_id = uuid.uuid4()
    await record_normalize_usage(fake_redis, user_id=user_id, tokens_used=0)
    report = await compute_normalize_budget_report(
        fake_redis, user_id=user_id, limits=BudgetLimits()
    )
    assert report.runs_in_last_24h == 1
    assert report.tokens_in_last_30d == 0


@pytest.mark.asyncio
async def test_record_swallows_redis_failure() -> None:
    """Telemetry — fire-and-forget: Redis-сбой не валит use-case."""

    class BrokenRedis:
        async def incr(self, *_: Any, **__: Any) -> int:
            err = "redis is down"
            raise ConnectionError(err)

        async def incrby(self, *_: Any, **__: Any) -> int:
            err = "redis is down"
            raise ConnectionError(err)

        async def expire(self, *_: Any, **__: Any) -> Any:
            err = "redis is down"
            raise ConnectionError(err)

    # Не должно бросать.
    await record_normalize_usage(
        BrokenRedis(),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        tokens_used=100,
    )


@pytest.mark.asyncio
async def test_compute_budget_fails_open_on_redis_error() -> None:
    """``compute_normalize_budget_report`` при сбое Redis возвращает zero-usage."""

    class BrokenRedis:
        async def get(self, *_: Any, **__: Any) -> Any:
            err = "redis is down"
            raise ConnectionError(err)

        async def mget(self, *_: Any, **__: Any) -> Any:
            err = "redis is down"
            raise ConnectionError(err)

    report = await compute_normalize_budget_report(
        BrokenRedis(),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        limits=BudgetLimits(max_runs_per_day=10, max_tokens_per_month=100_000),
    )
    assert report.runs_in_last_24h == 0
    assert report.tokens_in_last_30d == 0


@pytest.mark.asyncio
async def test_compute_budget_uses_now_override(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """``now`` override меняет день-bucket — позволяет тестам зафиксировать дату."""
    user_id = uuid.uuid4()
    fixed_now = dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=dt.UTC)
    await record_normalize_usage(fake_redis, user_id=user_id, tokens_used=100, now=fixed_now)
    report = await compute_normalize_budget_report(
        fake_redis, user_id=user_id, limits=BudgetLimits(), now=fixed_now
    )
    assert report.runs_in_last_24h == 1
    # Через день — старый bucket в окне 30d (tokens), но не в today (runs).
    next_day = fixed_now + dt.timedelta(days=1)
    report_next = await compute_normalize_budget_report(
        fake_redis, user_id=user_id, limits=BudgetLimits(), now=next_day
    )
    assert report_next.runs_in_last_24h == 0
    assert report_next.tokens_in_last_30d == 100


def test_build_place_normalizer_returns_instance() -> None:
    config = AILayerConfig(enabled=False)  # disabled OK для конструктора
    normalizer = build_place_normalizer(config)
    assert isinstance(normalizer, PlaceNormalizer)


def test_build_name_normalizer_returns_instance() -> None:
    config = AILayerConfig(enabled=False)
    normalizer = build_name_normalizer(config)
    assert isinstance(normalizer, NameNormalizer)
