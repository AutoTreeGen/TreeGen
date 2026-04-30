"""Тесты inline-search (Phase 14.2).

Pure-rendering и query-parsing тестируются без mock'ов. Handler
дёргается напрямую с MagicMock(InlineQuery) — никаких реальных aiogram
Bot/Dispatcher инстансов.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram_bot.services.db_queries import InlineSearchHit
from telegram_bot.services.handlers import (
    handle_inline_query,
    parse_inline_query,
    render_inline_results,
)

# -----------------------------------------------------------------------------
# parse_inline_query (pure)
# -----------------------------------------------------------------------------


def test_parse_empty_returns_empty_surname() -> None:
    surname, given, year = parse_inline_query("")
    assert surname == ""
    assert given is None
    assert year is None


def test_parse_only_surname() -> None:
    assert parse_inline_query("Ivanov") == ("Ivanov", None, None)


def test_parse_surname_year() -> None:
    assert parse_inline_query("Ivanov 1850") == ("Ivanov", None, 1850)


def test_parse_surname_given_year() -> None:
    assert parse_inline_query("Ivanov Alexei 1850") == ("Ivanov", "Alexei", 1850)


def test_parse_surname_year_first_then_given() -> None:
    """Year раньше given — given собирается из tail-токенов."""
    assert parse_inline_query("Ivanov 1850 Alexei") == ("Ivanov", "Alexei", 1850)


def test_parse_multi_word_given() -> None:
    assert parse_inline_query("Ivanov Alexei Petrovich") == (
        "Ivanov",
        "Alexei Petrovich",
        None,
    )


def test_parse_short_digits_not_year() -> None:
    """3-значный «год» — не год, идёт в given."""
    assert parse_inline_query("Ivanov 850") == ("Ivanov", "850", None)


def test_parse_strips_whitespace() -> None:
    assert parse_inline_query("   Ivanov   1850   ") == ("Ivanov", None, 1850)


# -----------------------------------------------------------------------------
# render_inline_results (pure)
# -----------------------------------------------------------------------------


def test_render_empty_hits_returns_empty_list() -> None:
    assert render_inline_results([], web_base_url="https://web.test") == []


def test_render_full_card() -> None:
    pid = uuid.uuid4()
    tid = uuid.uuid4()
    hits = [
        InlineSearchHit(
            id=pid,
            tree_id=tid,
            primary_name="Alexei Ivanov",
            birth_year=1850,
            birth_place_label="Moscow",
        )
    ]
    [article] = render_inline_results(hits, web_base_url="https://web.test")
    assert article.id == str(pid)
    assert article.title == "Alexei Ivanov"
    assert article.description == "1850 • Moscow"
    assert article.url == f"https://web.test/persons/{pid}?from=tg"
    # input_message_content — InputTextMessageContent с deep-link.
    msg = article.input_message_content
    assert "https://web.test/persons/" in msg.message_text  # type: ignore[union-attr]
    assert "?from=tg" in msg.message_text  # type: ignore[union-attr]


def test_render_no_birth_data_omits_description() -> None:
    hits = [
        InlineSearchHit(
            id=uuid.uuid4(),
            tree_id=uuid.uuid4(),
            primary_name="John Doe",
            birth_year=None,
            birth_place_label=None,
        )
    ]
    [article] = render_inline_results(hits, web_base_url="https://web.test")
    assert article.description is None


def test_render_only_year_no_place() -> None:
    hits = [
        InlineSearchHit(
            id=uuid.uuid4(),
            tree_id=uuid.uuid4(),
            primary_name="Anna",
            birth_year=1900,
            birth_place_label=None,
        )
    ]
    [article] = render_inline_results(hits, web_base_url="https://web.test")
    assert article.description == "1900"


def test_render_anonymous_person_uses_placeholder_title() -> None:
    hits = [
        InlineSearchHit(
            id=uuid.uuid4(),
            tree_id=uuid.uuid4(),
            primary_name=None,
            birth_year=None,
            birth_place_label=None,
        )
    ]
    [article] = render_inline_results(hits, web_base_url="https://web.test")
    assert article.title == "Без имени"


# -----------------------------------------------------------------------------
# handle_inline_query (mocked)
# -----------------------------------------------------------------------------


def _make_inline_query(*, query_text: str, from_user_id: int = 100) -> MagicMock:
    """Сконструировать MagicMock(InlineQuery) c .answer = AsyncMock()."""
    q = MagicMock()
    q.id = "q-id-1"
    q.query = query_text
    q.from_user = SimpleNamespace(id=from_user_id, is_bot=False, first_name="Test")
    q.answer = AsyncMock(return_value=None)
    return q


class _FakeSession:
    """Минимальный async-session с управляемыми return-value для тестов."""

    def __init__(self) -> None:
        self.user_id_for_chat: uuid.UUID | None = None
        self.search_result: tuple[uuid.UUID | None, list[InlineSearchHit]] = (None, [])

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _make_session_factory(session: _FakeSession) -> object:
    """Вернёт callable, который при `()` возвращает context-manager `session`."""

    def factory() -> _FakeSession:
        return session

    return factory


_UNLINKED_SHOULD_NOT_SEARCH = "search не должен дёргаться для unlinked user'а"
_EMPTY_SHOULD_NOT_SEARCH = "search не должен дёргаться без surname"


@pytest.mark.asyncio
async def test_inline_query_unlinked_user_shows_link_cta(monkeypatch) -> None:
    session = _FakeSession()

    async def fake_resolve(_session, *, tg_chat_id) -> None:
        assert tg_chat_id == 100

    async def fake_search(*_args, **_kwargs) -> None:
        raise AssertionError(_UNLINKED_SHOULD_NOT_SEARCH)

    monkeypatch.setattr("telegram_bot.services.handlers.resolve_user_id_from_chat", fake_resolve)
    monkeypatch.setattr(
        "telegram_bot.services.handlers.inline_search_persons_in_active_tree",
        fake_search,
    )

    iq = _make_inline_query(query_text="Ivanov")
    await handle_inline_query(
        iq,
        session_factory=_make_session_factory(session),
        web_base_url="https://web.test",
    )
    iq.answer.assert_awaited_once()
    kwargs = iq.answer.await_args.kwargs
    assert kwargs["results"] == []
    assert kwargs["switch_pm_text"] == "Link your account: /start"
    assert kwargs["switch_pm_parameter"] == "link"
    assert kwargs["is_personal"] is True


@pytest.mark.asyncio
async def test_inline_query_empty_query_shows_hint(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session = _FakeSession()

    async def fake_resolve(_session, *, tg_chat_id) -> uuid.UUID:  # noqa: ARG001
        return user_id

    async def fake_search(*_args, **_kwargs) -> None:
        raise AssertionError(_EMPTY_SHOULD_NOT_SEARCH)

    monkeypatch.setattr("telegram_bot.services.handlers.resolve_user_id_from_chat", fake_resolve)
    monkeypatch.setattr(
        "telegram_bot.services.handlers.inline_search_persons_in_active_tree",
        fake_search,
    )

    iq = _make_inline_query(query_text="   ")
    await handle_inline_query(
        iq,
        session_factory=_make_session_factory(session),
        web_base_url="https://web.test",
    )
    iq.answer.assert_awaited_once()
    kwargs = iq.answer.await_args.kwargs
    assert kwargs["results"] == []
    assert kwargs["switch_pm_parameter"] == "hint"


@pytest.mark.asyncio
async def test_inline_query_no_active_tree_shows_choose_tree(monkeypatch) -> None:
    user_id = uuid.uuid4()
    session = _FakeSession()

    async def fake_resolve(_session, *, tg_chat_id) -> uuid.UUID:  # noqa: ARG001
        return user_id

    async def fake_search(_session, **_kwargs) -> tuple[None, list[InlineSearchHit]]:
        return (None, [])

    monkeypatch.setattr("telegram_bot.services.handlers.resolve_user_id_from_chat", fake_resolve)
    monkeypatch.setattr(
        "telegram_bot.services.handlers.inline_search_persons_in_active_tree",
        fake_search,
    )

    iq = _make_inline_query(query_text="Ivanov")
    await handle_inline_query(
        iq,
        session_factory=_make_session_factory(session),
        web_base_url="https://web.test",
    )
    iq.answer.assert_awaited_once()
    kwargs = iq.answer.await_args.kwargs
    assert kwargs["switch_pm_text"] == "Choose a tree"
    assert kwargs["switch_pm_parameter"] == "dashboard"


@pytest.mark.asyncio
async def test_inline_query_with_year_filter_propagates(monkeypatch) -> None:
    user_id = uuid.uuid4()
    tree_id = uuid.uuid4()
    captured: dict[str, object] = {}
    session = _FakeSession()

    async def fake_resolve(_session, *, tg_chat_id) -> uuid.UUID:  # noqa: ARG001
        return user_id

    async def fake_search(_session, **kwargs) -> tuple[uuid.UUID, list[InlineSearchHit]]:
        captured.update(kwargs)
        return (
            tree_id,
            [
                InlineSearchHit(
                    id=uuid.uuid4(),
                    tree_id=tree_id,
                    primary_name="Alexei Ivanov",
                    birth_year=1850,
                    birth_place_label=None,
                )
            ],
        )

    monkeypatch.setattr("telegram_bot.services.handlers.resolve_user_id_from_chat", fake_resolve)
    monkeypatch.setattr(
        "telegram_bot.services.handlers.inline_search_persons_in_active_tree",
        fake_search,
    )

    iq = _make_inline_query(query_text="Ivanov Alexei 1850")
    await handle_inline_query(
        iq,
        session_factory=_make_session_factory(session),
        web_base_url="https://web.test",
    )
    assert captured["surname"] == "Ivanov"
    assert captured["given"] == "Alexei"
    assert captured["year"] == 1850
    assert captured["limit"] == 5

    iq.answer.assert_awaited_once()
    kwargs = iq.answer.await_args.kwargs
    assert len(kwargs["results"]) == 1
    article = kwargs["results"][0]
    assert article.title == "Alexei Ivanov"
    assert article.description == "1850"
