"""Юнит-тесты ``ProgressPublisher``.

Проверяем:

* публикуемый JSON содержит ожидаемые поля и сериализуется детерминированно;
* ``redis=None`` делает publisher no-op'ом;
* ``message=None`` опускается из payload, чтобы подписчики не получали
  лишний null-ключ.

В юнит-тестах используем минимальный stub вместо fakeredis-полного клиента —
нам нужен только метод ``publish``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from parser_service.services.progress import ProgressPublisher, Stage


class _CaptureRedis:
    """Stub-Redis: записывает все вызовы ``publish`` в список."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.calls.append((channel, message))
        return 1


@pytest.mark.asyncio
async def test_publish_emits_json_payload() -> None:
    """Опубликованное сообщение — JSON с полями stage/current/total/message."""
    redis = _CaptureRedis()
    publisher = ProgressPublisher(redis, "job-events:abc")

    await publisher.publish(Stage.PARSING, current=0, total=1, message="hi")

    assert len(redis.calls) == 1
    channel, body = redis.calls[0]
    assert channel == "job-events:abc"
    payload: dict[str, Any] = json.loads(body)
    assert payload == {
        "stage": "parsing",
        "current": 0,
        "total": 1,
        "message": "hi",
    }


@pytest.mark.asyncio
async def test_publish_omits_message_when_none() -> None:
    """``message`` отсутствует в payload, если caller не передал."""
    redis = _CaptureRedis()
    publisher = ProgressPublisher(redis, "job-events:abc")

    await publisher.publish(Stage.ENTITIES, current=10, total=10)

    payload = json.loads(redis.calls[0][1])
    assert "message" not in payload
    assert payload == {"stage": "entities", "current": 10, "total": 10}


@pytest.mark.asyncio
async def test_publish_accepts_string_stage() -> None:
    """Поддерживается передача стадии как строки (не только Stage-enum)."""
    redis = _CaptureRedis()
    publisher = ProgressPublisher(redis, "ch")

    await publisher.publish("custom-stage", current=1, total=2)

    payload = json.loads(redis.calls[0][1])
    assert payload["stage"] == "custom-stage"


@pytest.mark.asyncio
async def test_publisher_with_none_redis_is_noop() -> None:
    """Если redis=None — никакого вызова не происходит, ошибки тоже."""
    publisher = ProgressPublisher(None, "job-events:noop")
    assert publisher.is_enabled is False

    # Не падает, ничего не делает.
    await publisher.publish(Stage.FINALIZING, current=1, total=1, message="done")


@pytest.mark.asyncio
async def test_publisher_channel_property() -> None:
    """``channel`` отдаётся read-only через property."""
    publisher = ProgressPublisher(_CaptureRedis(), "job-events:42")
    assert publisher.channel == "job-events:42"
    assert publisher.is_enabled is True


@pytest.mark.asyncio
async def test_publish_with_fakeredis_pubsub_roundtrip() -> None:
    """Полный round-trip: publish → подписчик принимает сообщение.

    Использует fakeredis (in-memory Redis) для проверки, что наш payload
    действительно проходит через pub/sub layer и его форма не разойдётся
    с тем, что увидит SSE-консьюмер в api-gateway.
    """
    fakeredis = pytest.importorskip("fakeredis")

    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server)
    pubsub = redis.pubsub()
    channel = "job-events:roundtrip"
    await pubsub.subscribe(channel)
    # Первое сообщение — confirmation подписки; пропускаем.
    confirm = await pubsub.get_message(timeout=1.0)
    assert confirm is not None
    assert confirm["type"] == "subscribe"

    publisher = ProgressPublisher(redis, channel)
    await publisher.publish(Stage.PARSING, current=0, total=1, message="go")

    received = await pubsub.get_message(timeout=1.0)
    assert received is not None
    assert received["type"] == "message"
    payload = json.loads(received["data"])
    assert payload["stage"] == "parsing"
    assert payload["message"] == "go"

    await pubsub.unsubscribe(channel)
    await pubsub.aclose()
    await redis.aclose()
