"""Dispatcher: idempotency + channel routing + per-channel failure isolation.

Контракт:

* Dispatcher принимает запрос (user_id, event_type, payload, channels).
* Строит ``idempotency_key`` из ``payload.ref_id`` (или canonical-hash от
  payload, если ref_id не задан).
* Ищет существующую запись с тем же ``(user_id, event_type,
  idempotency_key)`` в ``idempotency_window_minutes`` — если найдена,
  возвращает её id и ранее зафиксированный ``delivered`` без второго
  INSERT.
* Иначе — создаёт ``Notification``, вызывает каналы по очереди (в
  порядке из request.channels), записывает успехи/ошибки в
  ``channels_attempted`` и проставляет ``delivered_at`` если хотя бы
  один канал прошёл успешно.

Channel failure isolation: исключение из одного канала ловится и
сериализуется в ``ChannelAttempt(success=False, error=str(exc))``,
остальные каналы продолжают выполняться.

CLAUDE.md §5: при channel failure доменные сущности НЕ откатываются —
у нотификации может быть delivered_at=None при всех неуспешных каналах,
но строка остаётся для retry-логики (Phase 8.x).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from shared_models.enums import NotificationEventType
from shared_models.orm import Notification
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service.channels.base import Channel
from notification_service.channels.in_app import InAppChannel
from notification_service.channels.log import LogChannel

_LOG = logging.getLogger(__name__)


# Реестр зарегистрированных каналов. Расширяется при появлении новых
# реализаций (Phase 8.1 EmailChannel и т. д.). Имя → инстанс.
# Типизируем явно как Channel — Protocol satisfaction для dict-literal
# values mypy сам не выводит в SubsclassesAreInstances-режиме.
def _build_channel_registry() -> dict[str, Channel]:
    in_app: Channel = InAppChannel()
    log: Channel = LogChannel()
    return {in_app.name: in_app, log.name: log}


_CHANNEL_REGISTRY: dict[str, Channel] = _build_channel_registry()

# Допустимые типы событий — материализуются из StrEnum в shared_models.enums.
_KNOWN_EVENT_TYPES: frozenset[str] = frozenset(e.value for e in NotificationEventType)


class UnknownChannelError(ValueError):
    """Запрошен channel, отсутствующий в реестре."""


class UnknownEventTypeError(ValueError):
    """Запрошен event_type, отсутствующий в NotificationEventType enum."""


@dataclass(frozen=True)
class DispatchOutcome:
    """Результат одной диспатч-операции (успех + идемпотентность)."""

    notification_id: Any
    delivered_channels: list[str]
    deduplicated: bool


def _canonical_idempotency_key(payload: dict[str, Any]) -> str:
    """Построить idempotency-ключ из payload.

    Если у payload есть ``ref_id`` (рекомендуемая convention для всех
    callers) — используем его как-есть. Иначе — sha1 от
    canonical-сериализованного payload. Для скелета этого достаточно;
    более тонкая логика (per-event-type ключи) — Phase 8.x.
    """
    ref_id = payload.get("ref_id")
    if ref_id is not None:
        return str(ref_id)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "h:" + hashlib.sha1(blob.encode("utf-8"), usedforsecurity=False).hexdigest()


def _normalize_channels(
    requested: Iterable[str],
) -> list[Channel]:
    """Преобразовать имена каналов в инстансы; неизвестное имя → 400.

    Сохраняем порядок, который пришёл от caller'а — это важно для
    тестов на channel failure isolation: если первым стоит ``log``,
    а вторым ``in_app``, мы хотим чтобы при падении ``log`` всё равно
    выполнился ``in_app``.
    """
    out: list[Channel] = []
    for name in requested:
        channel = _CHANNEL_REGISTRY.get(name)
        if channel is None:
            msg = f"Unknown channel: {name!r}. Known: {sorted(_CHANNEL_REGISTRY)}"
            raise UnknownChannelError(msg)
        out.append(channel)
    return out


async def _find_existing(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    idempotency_key: str,
    window_minutes: int,
) -> Notification | None:
    """Найти свежую нотификацию с тем же ключом в окне ``window_minutes``."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=window_minutes)
    res = await session.execute(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.event_type == event_type,
            Notification.idempotency_key == idempotency_key,
            Notification.created_at >= cutoff,
        )
        .order_by(Notification.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _run_channels(
    notification: Notification,
    channels: list[Channel],
) -> list[dict[str, Any]]:
    """Прогон через каналы с failure isolation.

    Возвращает массив элементов
    ``{"channel": str, "success": bool, "error": str|None,
       "attempted_at": iso8601}`` готовый сразу записать в
    ``Notification.channels_attempted``.
    """
    attempts: list[dict[str, Any]] = []
    for channel in channels:
        ts = dt.datetime.now(dt.UTC)
        try:
            success = bool(await channel.send(notification))
            error: str | None = None
        except Exception as exc:
            success = False
            error = f"{type(exc).__name__}: {exc}"
            _LOG.warning(
                "channel %s failed for notification %s: %s",
                channel.name,
                notification.id,
                error,
            )
        attempts.append(
            {
                "channel": channel.name,
                "success": success,
                "error": error,
                "attempted_at": ts.isoformat(),
            }
        )
    return attempts


async def dispatch(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: str,
    payload: dict[str, Any],
    channels: Iterable[str],
    idempotency_window_minutes: int = 60,
) -> DispatchOutcome:
    """Создать (или вернуть существующую) нотификацию и доставить её.

    Raises:
        UnknownEventTypeError: ``event_type`` не зарегистрирован в
            ``NotificationEventType``.
        UnknownChannelError: один из ``channels`` не зарегистрирован.

    На успешном пути:

    * Если найдена свежая нотификация с тем же idempotency-key —
      возвращаем её существующий ``id`` и ранее задеплоенные каналы;
      ``deduplicated=True``.
    * Иначе — INSERT + run channels + UPDATE channels_attempted /
      delivered_at; ``deduplicated=False``.
    """
    if event_type not in _KNOWN_EVENT_TYPES:
        msg = f"Unknown event_type: {event_type!r}. Known: {sorted(_KNOWN_EVENT_TYPES)}"
        raise UnknownEventTypeError(msg)

    channel_objs = _normalize_channels(channels)
    idempotency_key = _canonical_idempotency_key(payload)

    existing = await _find_existing(
        session,
        user_id=user_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        window_minutes=idempotency_window_minutes,
    )
    if existing is not None:
        delivered = [
            attempt["channel"]
            for attempt in (existing.channels_attempted or [])
            if attempt.get("success")
        ]
        return DispatchOutcome(
            notification_id=existing.id,
            delivered_channels=delivered,
            deduplicated=True,
        )

    notification = Notification(
        user_id=user_id,
        event_type=event_type,
        payload=payload,
        idempotency_key=idempotency_key,
        channels_attempted=[],
    )
    session.add(notification)
    await session.flush()  # получить id и created_at

    attempts = await _run_channels(notification, channel_objs)
    notification.channels_attempted = attempts
    if any(a["success"] for a in attempts):
        notification.delivered_at = dt.datetime.now(dt.UTC)
    await session.flush()

    delivered = [a["channel"] for a in attempts if a["success"]]
    return DispatchOutcome(
        notification_id=notification.id,
        delivered_channels=delivered,
        deduplicated=False,
    )
