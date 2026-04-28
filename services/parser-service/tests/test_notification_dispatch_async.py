"""Tests for arq-based notification dispatch (Phase 8.0 wire-up, ADR-0029).

Покрывает контракт parser_service.services.notifications:

* ``notify_hypothesis_pending_review`` enqueues arq job вместо
  fire-and-forget httpx.
* Empty AUTOTREEGEN_NOTIFICATION_SERVICE_URL → no enqueue (legacy
  light-integration mode сохраняется).
* ``dispatch_notification_job`` (worker-side) — успех/неуспех от
  ``post_notify_request`` относительно HTTP status.

Реальный Redis НЕ используется: pool monkeypatched на AsyncMock.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.asyncio]


# Disable autouse postgres + arq fixtures from parser-service conftest
# не нужны для этого pure-mocking теста, но они автоматически
# применятся; не страшно.


async def test_notify_hypothesis_enqueues_arq_job(monkeypatch) -> None:
    """notify_hypothesis_pending_review → enqueue_job('dispatch_notification_job', ...)."""
    # Включаем «notification-service настроен» режим — иначе функция
    # делает legacy-no-op без enqueue (ADR-0029 §«light-integration mode»).
    monkeypatch.setattr(
        "parser_service.services.notifications._DEFAULT_URL",
        "http://notify.test",
    )

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock()

    async def fake_get_pool() -> Any:
        return fake_pool

    monkeypatch.setattr(
        "parser_service.queue.get_arq_pool",
        fake_get_pool,
    )

    from parser_service.services.notifications import notify_hypothesis_pending_review

    user_id = uuid.uuid4()
    hypothesis_id = uuid.uuid4()
    tree_id = uuid.uuid4()

    await notify_hypothesis_pending_review(
        user_id=user_id,
        hypothesis_id=hypothesis_id,
        tree_id=tree_id,
        composite_score=0.83,
        hypothesis_type="same_person",
    )

    fake_pool.enqueue_job.assert_awaited_once()
    call = fake_pool.enqueue_job.call_args
    assert call.args[0] == "dispatch_notification_job"
    payload = call.args[1]
    assert payload["event_type"] == "hypothesis_pending_review"
    assert payload["user_id"] == user_id.int
    assert payload["payload"]["hypothesis_id"] == str(hypothesis_id)
    assert payload["payload"]["tree_id"] == str(tree_id)
    assert payload["payload"]["ref_id"] == str(hypothesis_id)
    assert payload["channels"] == ["in_app", "log"]


async def test_notify_hypothesis_skips_when_url_unset(monkeypatch) -> None:
    """Без AUTOTREEGEN_NOTIFICATION_SERVICE_URL — silent no-op (no enqueue)."""
    monkeypatch.setattr(
        "parser_service.services.notifications._DEFAULT_URL",
        "",
    )

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock()

    async def fake_get_pool() -> Any:
        return fake_pool

    monkeypatch.setattr(
        "parser_service.queue.get_arq_pool",
        fake_get_pool,
    )

    from parser_service.services.notifications import notify_hypothesis_pending_review

    await notify_hypothesis_pending_review(
        user_id=uuid.uuid4(),
        hypothesis_id=uuid.uuid4(),
        tree_id=uuid.uuid4(),
        composite_score=0.5,
        hypothesis_type="same_person",
    )

    # Никаких enqueue'ов — функция должна была вернуться рано.
    fake_pool.enqueue_job.assert_not_awaited()


async def test_notify_hypothesis_swallows_enqueue_failure(monkeypatch, caplog) -> None:
    """Падение enqueue (Redis down etc.) логируется, но не пробрасывается.

    Это критично: notification — best-effort, доменная транзакция
    hypothesis_runner НЕ должна откатываться из-за нотификаций
    (см. ADR-0029 §«Последствия / Отрицательные»).
    """
    monkeypatch.setattr(
        "parser_service.services.notifications._DEFAULT_URL",
        "http://notify.test",
    )

    async def boom() -> Any:
        msg = "redis offline"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "parser_service.queue.get_arq_pool",
        boom,
    )

    from parser_service.services.notifications import notify_hypothesis_pending_review

    # Не должно пробросить.
    await notify_hypothesis_pending_review(
        user_id=uuid.uuid4(),
        hypothesis_id=uuid.uuid4(),
        tree_id=uuid.uuid4(),
        composite_score=0.5,
        hypothesis_type="same_person",
    )
    # Лог-warning есть. ``record.getMessage()`` — formatted версия, ``record.message``
    # пустой пока не пройдёт через Formatter, поэтому каплог-проверки используют get.
    assert any("failed to enqueue notification" in record.getMessage() for record in caplog.records)


async def test_dispatch_notification_job_returns_delivered_on_2xx(monkeypatch) -> None:
    """Worker-side: 2xx от notification-service → delivered=True."""
    monkeypatch.setattr(
        "parser_service.services.notifications._DEFAULT_URL",
        "http://notify.test",
    )

    async def fake_post(_payload: dict[str, Any], *, base_url: str | None = None) -> bool:
        del base_url
        return True

    monkeypatch.setattr(
        "parser_service.worker.post_notify_request",
        fake_post,
    )

    from parser_service.worker import dispatch_notification_job

    result = await dispatch_notification_job(
        {},
        {"event_type": "hypothesis_pending_review", "user_id": 42},
    )
    assert result == {
        "event_type": "hypothesis_pending_review",
        "user_id": 42,
        "delivered": True,
    }


async def test_dispatch_notification_job_propagates_5xx_for_retry(monkeypatch) -> None:
    """5xx от notification-service → исключение → arq возьмёт на ретрай."""
    import httpx

    async def fake_post_raises(
        _payload: dict[str, Any],
        *,
        base_url: str | None = None,
    ) -> bool:
        del base_url
        msg = "boom"
        raise httpx.ConnectError(msg)

    monkeypatch.setattr(
        "parser_service.worker.post_notify_request",
        fake_post_raises,
    )

    from parser_service.worker import dispatch_notification_job

    with pytest.raises(httpx.HTTPError):
        await dispatch_notification_job(
            {},
            {"event_type": "hypothesis_pending_review", "user_id": 42},
        )
