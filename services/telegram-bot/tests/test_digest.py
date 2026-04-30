"""Тесты weekly digest worker'а (Phase 14.2).

* Pure render-функции тестируются без mock'ов (ru/en, empty top, etc).
* ``send_weekly_digest`` — с fakeredis + AsyncMock(httpx) + AsyncMock(Bot).
  Сессия мокается так, чтобы ``.execute(...)`` возвращал nameable rows
  ``(TelegramUserLink, User)``.
* Cron-schedule проверяется через ``WorkerSettings.cron_jobs``.
* ``handle_digest_unsubscribe`` callback — fakeredis + mock session.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import httpx
import pytest
from aiogram.exceptions import TelegramAPIError
from telegram_bot.jobs.digest import (
    build_unsubscribe_keyboard,
    optout_redis_key,
    render_digest_message,
    send_weekly_digest,
    sent_redis_key,
)
from telegram_bot.services.handlers import handle_digest_unsubscribe
from telegram_bot.worker import WorkerSettings

# -----------------------------------------------------------------------------
# render_digest_message (pure)
# -----------------------------------------------------------------------------


def test_render_ru_with_data() -> None:
    text = render_digest_message(
        locale="ru",
        new_persons_count=12,
        new_hypotheses_pending=3,
        top_persons=[
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "tree_id": "22222222-2222-2222-2222-222222222222",
                "primary_name": "Иван Петров",
                "birth_year": 1850,
            },
        ],
        web_base_url="https://web.test",
    )
    assert "Дайджест за неделю" in text
    assert "12 новых персон" in text
    assert "3 гипотез" in text
    assert "Иван Петров" in text
    assert "1850" in text
    assert "https://web.test/persons/11111111-1111-1111-1111-111111111111?from=tg" in text


def test_render_en_with_data() -> None:
    text = render_digest_message(
        locale="en",
        new_persons_count=5,
        new_hypotheses_pending=0,
        top_persons=[],
        web_base_url="https://web.test",
    )
    assert "Weekly digest" in text
    assert "5 new persons" in text
    assert "0 hypotheses await review" in text
    assert "No new person cards this week." in text


def test_render_unknown_locale_falls_back_to_en() -> None:
    text = render_digest_message(
        locale="fr",
        new_persons_count=1,
        new_hypotheses_pending=0,
        top_persons=[],
        web_base_url="https://web.test",
    )
    assert "Weekly digest" in text


def test_render_omits_year_when_none() -> None:
    text = render_digest_message(
        locale="en",
        new_persons_count=1,
        new_hypotheses_pending=0,
        top_persons=[
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "tree_id": "44444444-4444-4444-4444-444444444444",
                "primary_name": "Anna",
                "birth_year": None,
            },
        ],
        web_base_url="https://web.test",
    )
    assert "Anna" in text
    assert "(None)" not in text


def test_unsubscribe_keyboard_callback_data_is_stable() -> None:
    keyboard = build_unsubscribe_keyboard("ru")
    [[button]] = keyboard.inline_keyboard
    assert button.callback_data == "digest:unsubscribe"
    assert "Отписаться" in button.text


# -----------------------------------------------------------------------------
# Cron schedule (Phase 14.2 spec: каждый понедельник 09:00 UTC)
# -----------------------------------------------------------------------------


def test_cron_registered_for_monday_09_utc() -> None:
    """Cron-jobs зарегистрирован на send_weekly_digest, понедельник 09:00."""
    [job] = WorkerSettings.cron_jobs
    # arq.cron возвращает Function-объект; точный shape зависит от версии,
    # но ``.coroutine``/``.name`` стабильны.
    name = getattr(job, "name", None) or getattr(job, "coroutine", None).__name__
    assert "send_weekly_digest" in name

    assert job.weekday == "mon"
    assert job.hour == 9
    assert job.minute == 0


# -----------------------------------------------------------------------------
# send_weekly_digest (mocked deps)
# -----------------------------------------------------------------------------


def _make_user(*, locale: str = "en") -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        locale=locale,
        deleted_at=None,
    )


def _make_link(*, user_id: uuid.UUID, chat_id: int) -> Any:
    return SimpleNamespace(
        user_id=user_id,
        tg_chat_id=chat_id,
        revoked_at=None,
        notifications_enabled=True,
    )


class _FakeSessionResult:
    """Эмулирует SQLAlchemy `Result.all()` → list[(link, user)]."""

    def __init__(self, rows: list[tuple[Any, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, Any]]:
        return self._rows


class _FakeSession:
    """Минимальная async-session, возвращает заранее заданный list rows."""

    def __init__(self, rows: list[tuple[Any, Any]]) -> None:
        self._rows = rows

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, _stmt: Any) -> _FakeSessionResult:
        return _FakeSessionResult(self._rows)


def _make_session_factory(rows: list[tuple[Any, Any]]) -> Any:
    def factory() -> _FakeSession:
        return _FakeSession(rows)

    return factory


def _summary_response(
    *,
    new_persons: int = 0,
    pending: int = 0,
    top: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "user_id": str(uuid.uuid4()),
        "since": dt.datetime.now(dt.UTC).isoformat(),
        "new_persons_count": new_persons,
        "new_hypotheses_pending": pending,
        "top_3_recent_persons": top or [],
    }


def _make_http_client_returning(
    body: dict[str, Any] | None,
    *,
    status_code: int = 200,
) -> MagicMock:
    """httpx.AsyncClient mock: ``client.get`` → resp с фиксированным body."""
    client = MagicMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body or {})
    resp.text = "<resp>"
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_send_weekly_digest_misconfigured_token_returns_skipped() -> None:
    """Без ``parser_token`` worker не делает HTTP-вызовов и сразу выходит."""
    redis = fakeredis.aioredis.FakeRedis()
    bot = MagicMock()
    bot.send_message = AsyncMock()

    ctx = {
        "bot": bot,
        "redis": redis,
        "http_client": _make_http_client_returning(None),
        "session_factory": _make_session_factory([]),
        "parser_base_url": "http://parser:8000",
        "parser_token": "",  # пусто
        "web_base_url": "https://web.test",
    }
    stats = await send_weekly_digest(ctx)
    assert stats == {"skipped_misconfigured": 1}
    bot.send_message.assert_not_called()
    await redis.aclose()


@pytest.mark.asyncio
async def test_send_weekly_digest_optout_skipped() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    user = _make_user()
    link = _make_link(user_id=user.id, chat_id=999)
    await redis.set(optout_redis_key(user.id), "1")

    ctx = {
        "bot": bot,
        "redis": redis,
        "http_client": _make_http_client_returning(_summary_response(new_persons=5)),
        "session_factory": _make_session_factory([(link, user)]),
        "parser_base_url": "http://parser:8000",
        "parser_token": "secret",
        "web_base_url": "https://web.test",
    }
    stats = await send_weekly_digest(ctx)
    assert stats == {"skipped_optout": 1}
    bot.send_message.assert_not_called()
    await redis.aclose()


@pytest.mark.asyncio
async def test_send_weekly_digest_already_sent_idempotent() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    user = _make_user()
    link = _make_link(user_id=user.id, chat_id=999)

    period_start = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    await redis.set(sent_redis_key(user.id, period_start), "1")

    ctx = {
        "bot": bot,
        "redis": redis,
        "http_client": _make_http_client_returning(_summary_response(new_persons=10)),
        "session_factory": _make_session_factory([(link, user)]),
        "parser_base_url": "http://parser:8000",
        "parser_token": "secret",
        "web_base_url": "https://web.test",
    }
    stats = await send_weekly_digest(ctx)
    assert stats == {"skipped_already_sent": 1}
    bot.send_message.assert_not_called()
    await redis.aclose()


@pytest.mark.asyncio
async def test_send_weekly_digest_empty_summary_skipped_but_marks_idempotent() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    user = _make_user()
    link = _make_link(user_id=user.id, chat_id=999)

    ctx = {
        "bot": bot,
        "redis": redis,
        "http_client": _make_http_client_returning(_summary_response()),
        "session_factory": _make_session_factory([(link, user)]),
        "parser_base_url": "http://parser:8000",
        "parser_token": "secret",
        "web_base_url": "https://web.test",
    }
    stats = await send_weekly_digest(ctx)
    assert stats == {"skipped_empty": 1}
    bot.send_message.assert_not_called()

    # Idempotency-флаг проставлен: повторный run не дёрнет parser снова.
    period_start = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    flag = await redis.get(sent_redis_key(user.id, period_start))
    assert flag == b"1"
    await redis.aclose()


@pytest.mark.asyncio
async def test_send_weekly_digest_sends_html_with_locale_keyboard() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    user = _make_user(locale="ru")
    link = _make_link(user_id=user.id, chat_id=42)

    ctx = {
        "bot": bot,
        "redis": redis,
        "http_client": _make_http_client_returning(
            _summary_response(
                new_persons=12,
                pending=3,
                top=[
                    {
                        "id": "55555555-5555-5555-5555-555555555555",
                        "tree_id": "66666666-6666-6666-6666-666666666666",
                        "primary_name": "Иван Петров",
                        "birth_year": 1850,
                    }
                ],
            )
        ),
        "session_factory": _make_session_factory([(link, user)]),
        "parser_base_url": "http://parser:8000",
        "parser_token": "secret",
        "web_base_url": "https://web.test",
    }
    stats = await send_weekly_digest(ctx)
    assert stats == {"sent": 1}

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["parse_mode"] == "HTML"
    assert "Дайджест за неделю" in kwargs["text"]
    assert "12 новых персон" in kwargs["text"]
    # Кнопка отписки — единственная.
    [[button]] = kwargs["reply_markup"].inline_keyboard
    assert button.callback_data == "digest:unsubscribe"
    await redis.aclose()


@pytest.mark.asyncio
async def test_send_weekly_digest_telegram_error_does_not_mark_idempotency() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=TelegramAPIError(method=MagicMock(), message="boom"))
    user = _make_user()
    link = _make_link(user_id=user.id, chat_id=42)

    ctx = {
        "bot": bot,
        "redis": redis,
        "http_client": _make_http_client_returning(_summary_response(new_persons=5)),
        "session_factory": _make_session_factory([(link, user)]),
        "parser_base_url": "http://parser:8000",
        "parser_token": "secret",
        "web_base_url": "https://web.test",
    }
    stats = await send_weekly_digest(ctx)
    assert stats == {"skipped_send_error": 1}

    period_start = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    flag = await redis.get(sent_redis_key(user.id, period_start))
    assert flag is None  # не выставлено — следующая cron-tick попробует ещё раз
    await redis.aclose()


@pytest.mark.asyncio
async def test_send_weekly_digest_api_error_skipped() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    user = _make_user()
    link = _make_link(user_id=user.id, chat_id=42)

    ctx = {
        "bot": bot,
        "redis": redis,
        "http_client": _make_http_client_returning(None, status_code=503),
        "session_factory": _make_session_factory([(link, user)]),
        "parser_base_url": "http://parser:8000",
        "parser_token": "secret",
        "web_base_url": "https://web.test",
    }
    stats = await send_weekly_digest(ctx)
    assert stats == {"skipped_api_error": 1}
    bot.send_message.assert_not_called()
    await redis.aclose()


# -----------------------------------------------------------------------------
# handle_digest_unsubscribe (callback handler)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_digest_unsubscribe_sets_redis_flag(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis()
    user_id = uuid.uuid4()

    async def fake_resolve(_session, *, tg_chat_id) -> uuid.UUID:  # noqa: ARG001
        return user_id

    monkeypatch.setattr("telegram_bot.services.handlers.resolve_user_id_from_chat", fake_resolve)

    callback = MagicMock()
    callback.message = SimpleNamespace(chat=SimpleNamespace(id=42))
    callback.answer = AsyncMock()

    class _Sf:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return MagicMock()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    def factory():  # type: ignore[no-untyped-def]
        return _Sf()

    await handle_digest_unsubscribe(callback, session_factory=factory, redis=redis)

    assert await redis.get(f"digest:optout:{user_id}") == b"1"
    callback.answer.assert_awaited_once()
    await redis.aclose()


@pytest.mark.asyncio
async def test_handle_digest_unsubscribe_unknown_user_no_flag(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis()

    async def fake_resolve(_session, *, tg_chat_id) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("telegram_bot.services.handlers.resolve_user_id_from_chat", fake_resolve)

    callback = MagicMock()
    callback.message = SimpleNamespace(chat=SimpleNamespace(id=42))
    callback.answer = AsyncMock()

    class _Sf:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return MagicMock()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    def factory():  # type: ignore[no-untyped-def]
        return _Sf()

    await handle_digest_unsubscribe(callback, session_factory=factory, redis=redis)

    keys = [k async for k in redis.scan_iter(match="digest:optout:*")]
    assert keys == []
    callback.answer.assert_awaited_once()
    args = callback.answer.await_args
    assert "не найдена" in args.args[0].lower()
    await redis.aclose()
