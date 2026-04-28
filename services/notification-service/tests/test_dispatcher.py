"""Dispatcher unit tests — idempotency + channel failure isolation.

Эти тесты разговаривают с реальной БД через session fixture (для
`Notification` ORM-операций), но не с FastAPI — dispatcher вызывается
напрямую как библиотека.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from notification_service.services.dispatcher import (
    _CHANNEL_REGISTRY,
    UnknownChannelError,
    UnknownEventTypeError,
    dispatch,
)
from shared_models.orm import Notification, NotificationPreference
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def session(postgres_dsn: str) -> AsyncIterator:
    """Свежая async-сессия + commit per test (dispatcher делает свой flush)."""
    engine = create_async_engine(postgres_dsn, future=True, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


async def test_dispatch_creates_notification_and_runs_channels(session) -> None:
    outcome = await dispatch(
        session,
        user_id=1,
        event_type="hypothesis_pending_review",
        payload={"ref_id": "h-1", "hypothesis_id": 42, "tree_id": 7},
        channels=["in_app", "log"],
    )
    assert outcome.deduplicated is False
    assert sorted(outcome.delivered_channels) == ["in_app", "log"]

    row = (
        await session.execute(
            select(Notification).where(Notification.id == outcome.notification_id)
        )
    ).scalar_one()
    assert row.user_id == 1
    assert row.event_type == "hypothesis_pending_review"
    assert row.delivered_at is not None
    assert {a["channel"] for a in row.channels_attempted} == {"in_app", "log"}
    assert all(a["success"] for a in row.channels_attempted)


async def test_dispatch_idempotent_within_window(session) -> None:
    """Повторный вызов с тем же ref_id за окно 1 час не создаёт новую строку."""
    common = {
        "user_id": 2,
        "event_type": "dna_match_found",
        "payload": {"ref_id": "match-99", "match_id": 99},
        "channels": ["in_app"],
    }
    first = await dispatch(session, **common, idempotency_window_minutes=60)
    second = await dispatch(session, **common, idempotency_window_minutes=60)

    assert first.deduplicated is False
    assert second.deduplicated is True
    assert first.notification_id == second.notification_id

    rows = (
        (await session.execute(select(Notification).where(Notification.user_id == 2)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_dispatch_different_ref_id_creates_separate_rows(session) -> None:
    """ref_id меняется → idempotency-ключ меняется → две разные строки."""
    base = {
        "user_id": 3,
        "event_type": "hypothesis_pending_review",
        "channels": ["in_app"],
    }
    a = await dispatch(session, payload={"ref_id": "h-1"}, **base)
    b = await dispatch(session, payload={"ref_id": "h-2"}, **base)
    assert a.notification_id != b.notification_id

    rows = (
        (await session.execute(select(Notification).where(Notification.user_id == 3)))
        .scalars()
        .all()
    )
    assert len(rows) == 2


async def test_dispatch_unknown_event_type_raises(session) -> None:
    with pytest.raises(UnknownEventTypeError):
        await dispatch(
            session,
            user_id=4,
            event_type="not_a_real_type",
            payload={"ref_id": "x"},
            channels=["in_app"],
        )


async def test_dispatch_unknown_channel_raises(session) -> None:
    with pytest.raises(UnknownChannelError):
        await dispatch(
            session,
            user_id=5,
            event_type="hypothesis_pending_review",
            payload={"ref_id": "x"},
            channels=["pigeon_post"],
        )


async def test_dispatch_skipped_when_preference_disabled(session) -> None:
    """User отключил event_type → DispatchOutcome без notifications-row.

    ADR-0029: «не создаём row для отключённых типов» — иначе раздуваем
    inbox и unread-counter показывает события, которые user явно
    подавил.
    """
    session.add(
        NotificationPreference(
            user_id=701,
            event_type="hypothesis_pending_review",
            enabled=False,
            channels=["in_app", "log"],
        )
    )
    await session.flush()

    outcome = await dispatch(
        session,
        user_id=701,
        event_type="hypothesis_pending_review",
        payload={"ref_id": "h-skipped"},
        channels=["in_app", "log"],
    )

    assert outcome.skipped_by_pref is True
    assert outcome.notification_id is None
    assert outcome.delivered_channels == []
    assert outcome.deduplicated is False

    # Главное: row в БД не появилось.
    rows = (
        (await session.execute(select(Notification).where(Notification.user_id == 701)))
        .scalars()
        .all()
    )
    assert rows == []


async def test_dispatch_drops_channels_outside_preference(session) -> None:
    """``prefs.channels`` пересекается с requested — лишнее идёт в attempts.

    Если pref-запись разрешает только ``in_app``, а caller запросил
    ``["in_app", "log"]`` — log пропускается с ``skipped: user_pref``,
    in_app доставляется. delivered_at проставляется (есть успешный канал).
    """
    session.add(
        NotificationPreference(
            user_id=702,
            event_type="dna_match_found",
            enabled=True,
            channels=["in_app"],
        )
    )
    await session.flush()

    outcome = await dispatch(
        session,
        user_id=702,
        event_type="dna_match_found",
        payload={"ref_id": "match-channel-test"},
        channels=["in_app", "log"],
    )

    assert outcome.skipped_by_pref is False
    assert outcome.delivered_channels == ["in_app"]

    row = (
        await session.execute(
            select(Notification).where(Notification.id == outcome.notification_id)
        )
    ).scalar_one()
    by_channel = {a["channel"]: a for a in row.channels_attempted}
    assert by_channel["in_app"]["success"] is True
    assert by_channel["log"]["success"] is False
    assert by_channel["log"].get("skipped") == "user_pref"
    # Несмотря на «pref-skip log» — delivered_at установлен (in_app ok).
    assert row.delivered_at is not None


async def test_channel_failure_isolation(session, monkeypatch) -> None:
    """Падение одного канала не ломает остальные.

    Подменяем ``LogChannel.send`` так, чтобы он всегда выкидывал
    исключение. ``in_app`` рядом должен пройти и проставить
    ``delivered_at``; запись в ``channels_attempted`` для log должна
    содержать ``success=False`` + ``error``.
    """
    log_channel = _CHANNEL_REGISTRY["log"]

    async def boom(_self, _notification):
        msg = "log sink offline"
        raise RuntimeError(msg)

    monkeypatch.setattr(type(log_channel), "send", boom)

    outcome = await dispatch(
        session,
        user_id=6,
        event_type="import_completed",
        payload={"ref_id": "imp-1"},
        channels=["log", "in_app"],  # log первым — должен упасть, in_app — пройти
    )
    assert outcome.delivered_channels == ["in_app"]
    assert outcome.deduplicated is False

    row = (
        await session.execute(
            select(Notification).where(Notification.id == outcome.notification_id)
        )
    ).scalar_one()
    by_channel = {a["channel"]: a for a in row.channels_attempted}
    assert by_channel["log"]["success"] is False
    assert by_channel["log"]["error"]
    assert "RuntimeError" in by_channel["log"]["error"]
    assert by_channel["in_app"]["success"] is True
    # delivered_at проставлен (в ≥ 1 канал ok).
    assert row.delivered_at is not None
