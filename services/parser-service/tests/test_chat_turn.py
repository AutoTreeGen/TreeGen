"""Phase 10.7c — integration tests для AI chat turn endpoint.

Покрытие:

* happy path — POST creates new session, stream'ит token-кадры, persist'ит
  user + assistant messages, финальный done-кадр содержит referenced_persons.
* missing-anchor — 409 если ``trees.owner_person_id is null`` и в request'е
  тоже не указан anchor.
* anthropic-error — terminal error-кадр в SSE-стриме при exception'е из
  AnthropicClient.
* persistence — после done-кадра в БД лежат две row'ы (user + assistant)
  с правильными ролями и references_jsonb.

Anthropic мокается через ``app.dependency_overrides[get_anthropic_client]``;
ни один тест не делает сетевых вызовов.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
import pytest_asyncio
from parser_service.api.chat import get_anthropic_client
from shared_models import TreeRole
from shared_models.orm import (
    ChatMessage,
    ChatSession,
    Family,
    Name,
    Person,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]

_FRAME_SEP = re.compile(r"\r?\n\r?\n")


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> AsyncIterator[Any]:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"chat-{uuid.uuid4().hex[:8]}@example.com"
    async with factory() as session:
        user = User(
            email=e,
            external_auth_id=f"local:{e}",
            display_name=e.split("@", 1)[0],
            locale="en",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_tree_with_owner_person(
    factory: Any,
    *,
    owner: User,
    set_anchor: bool = True,
) -> tuple[Tree, Person | None]:
    """Tree + OWNER membership + опциональный anchor person + spouse + child."""
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Chat Test {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=owner.id,
                role=TreeRole.OWNER.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        if not set_anchor:
            await session.commit()
            await session.refresh(tree)
            return tree, None

        anchor = Person(tree_id=tree.id, sex="M", provenance={}, version_id=1)
        spouse = Person(tree_id=tree.id, sex="F", provenance={}, version_id=1)
        child = Person(tree_id=tree.id, sex="F", provenance={}, version_id=1)
        session.add_all([anchor, spouse, child])
        await session.flush()
        # Имена для name-index'а: anchor "Vladimir Z", spouse "Olga Z".
        session.add_all(
            [
                Name(
                    person_id=anchor.id,
                    given_name="Vladimir",
                    surname="Z",
                    name_type="birth",
                    sort_order=0,
                ),
                Name(
                    person_id=spouse.id,
                    given_name="Olga",
                    surname="Z",
                    name_type="birth",
                    sort_order=0,
                ),
                Name(
                    person_id=child.id,
                    given_name="Dvora",
                    surname="Z",
                    name_type="birth",
                    sort_order=0,
                ),
            ]
        )
        family = Family(
            tree_id=tree.id,
            husband_id=anchor.id,
            wife_id=spouse.id,
            provenance={},
            version_id=1,
        )
        session.add(family)
        await session.flush()
        from shared_models.enums import RelationType
        from shared_models.orm import FamilyChild

        session.add(
            FamilyChild(
                family_id=family.id,
                child_person_id=child.id,
                relation_type=RelationType.BIOLOGICAL.value,
                birth_order=1,
            )
        )
        # Set anchor.
        tree.owner_person_id = anchor.id
        await session.commit()
        await session.refresh(tree)
        await session.refresh(anchor)
        return tree, anchor


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


class _StubAnthropicClient:
    """Stub-AnthropicClient: даёт детерминированные text-deltas без сети."""

    def __init__(self, deltas: Sequence[str] | None = None, *, raise_at: int | None = None) -> None:
        self._deltas = list(deltas) if deltas is not None else ["Hello, ", "Vladimir."]
        self._raise_at = raise_at

    async def stream_completion(
        self,
        *,
        system: str,  # noqa: ARG002 — interface stub mirrors AnthropicClient signature.
        messages: Sequence[dict[str, str]],  # noqa: ARG002
        model: str | None = None,  # noqa: ARG002
        max_tokens: int = 1024,  # noqa: ARG002
        temperature: float = 0.0,  # noqa: ARG002
    ) -> AsyncIterator[str]:
        msg = "stub LLM error"
        for i, delta in enumerate(self._deltas):
            if self._raise_at is not None and i == self._raise_at:
                raise RuntimeError(msg)
            yield delta


def _install_stub(app: Any, stub: _StubAnthropicClient) -> None:
    app.dependency_overrides[get_anthropic_client] = lambda: stub


def _clear_stub(app: Any) -> None:
    app.dependency_overrides.pop(get_anthropic_client, None)


def _parse_sse(text: str) -> list[dict[str, Any]]:
    """Парсит SSE-стрим (concatenated frames) в list of dict-payloads.

    sse-starlette шлёт ``\\r\\n\\r\\n`` (CRLF) как разделитель кадров —
    HTTP-канон. Делим по обоим (``\\r\\n\\r\\n`` и ``\\n\\n``) для робастности.
    """
    frames: list[dict[str, Any]] = []
    for segment in _FRAME_SEP.split(text):
        data_lines: list[str] = []
        for raw_line in segment.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        if not data_lines:
            continue
        try:
            frames.append(json.loads("\n".join(data_lines)))
        except json.JSONDecodeError:
            continue
    return frames


async def _post_and_collect_sse(
    client: Any,
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str],
) -> tuple[int, list[dict[str, Any]], str]:
    """POST + drain SSE stream, return (status_code, parsed_frames, raw_body).

    EventSourceResponse + httpx ASGITransport требует stream-mode для
    корректной агрегации кадров; обычный ``client.post`` иногда возвращает
    пустой body на ASGI-стороне.
    """
    frames: list[dict[str, Any]] = []
    buffer = ""
    raw_chunks: list[str] = []
    async with client.stream("POST", url, json=json_body, headers=headers) as response:
        if response.status_code != 200:
            body = (await response.aread()).decode("utf-8", errors="replace")
            return response.status_code, frames, body
        async for chunk in response.aiter_text():
            raw_chunks.append(chunk)
            buffer += chunk
            # Вытаскиваем все завершённые кадры (CRLF/LF-разделитель).
            while True:
                match = _FRAME_SEP.search(buffer)
                if match is None:
                    break
                segment = buffer[: match.start()]
                buffer = buffer[match.end() :]
                frames.extend(_parse_sse(segment))
        if buffer.strip():
            frames.extend(_parse_sse(buffer))
    return response.status_code, frames, "".join(raw_chunks)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_turn_happy_path(app, app_client, session_factory: Any) -> None:
    """POST /trees/{id}/chat/turn без session_id → создаёт session, стримит токены, done-кадр."""
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_owner_person(session_factory, owner=owner)
    assert anchor is not None

    stub = _StubAnthropicClient(deltas=["Your wife ", "is Olga."])
    _install_stub(app, stub)
    try:
        status_code, frames, raw_body = await _post_and_collect_sse(
            app_client,
            f"/trees/{tree.id}/chat/turn",
            json_body={
                "session_id": None,
                "message": "Tell me about my wife.",
                "anchor_person_id": str(anchor.id),
            },
            headers=_hdr(owner),
        )
    finally:
        _clear_stub(app)

    assert status_code == 200, raw_body
    types = [f["type"] for f in frames]
    assert types, f"empty frame list; raw={raw_body!r}"
    assert types[0] == "session", frames
    assert "token" in types, frames
    assert types[-1] == "done", frames

    session_frame = frames[0]
    assert session_frame["anchor_person_id"] == str(anchor.id)
    session_id = uuid.UUID(session_frame["session_id"])

    done_frame = frames[-1]
    assert "message_id" in done_frame
    # У user-input "Tell me about my wife" ego_resolver должен резолвить
    # "my wife" → spouse person.
    refs = done_frame["referenced_persons"]
    assert isinstance(refs, list)

    # Session row создалась.
    async with session_factory() as session:
        sess = await session.get(ChatSession, session_id)
        assert sess is not None
        assert sess.tree_id == tree.id
        assert sess.user_id == owner.id


@pytest.mark.asyncio
async def test_chat_turn_missing_anchor_returns_409(
    app,
    app_client,
    session_factory: Any,
) -> None:
    """Если у tree нет owner_person_id и анchor не указан в request'е → 409."""
    owner = await _make_user(session_factory)
    tree, _ = await _make_tree_with_owner_person(session_factory, owner=owner, set_anchor=False)

    stub = _StubAnthropicClient()
    _install_stub(app, stub)
    try:
        async with app_client.stream(
            "POST",
            f"/trees/{tree.id}/chat/turn",
            json={
                "session_id": None,
                "message": "Hello",
                "anchor_person_id": None,
            },
            headers=_hdr(owner),
        ) as response:
            body = await response.aread()
            assert response.status_code == 409, body
            payload = json.loads(body)
            assert "self-anchor" in payload["detail"].lower()
    finally:
        _clear_stub(app)


@pytest.mark.asyncio
async def test_chat_turn_anthropic_error_emits_error_frame(
    app,
    app_client,
    session_factory: Any,
) -> None:
    """LLM exception → terminal error-кадр; user message всё равно persisted."""
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_owner_person(session_factory, owner=owner)
    assert anchor is not None

    # raise_at=0 — фейлимся до первого yield.
    stub = _StubAnthropicClient(deltas=["x"], raise_at=0)
    _install_stub(app, stub)
    try:
        status_code, frames, raw_body = await _post_and_collect_sse(
            app_client,
            f"/trees/{tree.id}/chat/turn",
            json_body={
                "session_id": None,
                "message": "Anything",
                "anchor_person_id": str(anchor.id),
            },
            headers=_hdr(owner),
        )
    finally:
        _clear_stub(app)

    assert status_code == 200, raw_body  # SSE-stream открылся; ошибка в frame'е.
    types = [f["type"] for f in frames]
    assert types[0] == "session", frames
    assert types[-1] == "error", frames
    assert "stub LLM error" in frames[-1]["detail"] or "LLM error" in frames[-1]["detail"]

    # User-сообщение всё равно persisted (контракт: пишем user-side ДО
    # стрима, чтобы history была консистентна при сбое).
    session_id = uuid.UUID(frames[0]["session_id"])
    async with session_factory() as session:
        rows = (
            (await session.execute(select(ChatMessage).where(ChatMessage.session_id == session_id)))
            .scalars()
            .all()
        )
        roles = {row.role for row in rows}
        assert "user" in roles
        # Assistant row НЕ должен быть persisted, т.к. стрим упал до полного
        # ответа.
        assert "assistant" not in roles


@pytest.mark.asyncio
async def test_chat_turn_persists_user_and_assistant_with_correct_shape(
    app,
    app_client,
    session_factory: Any,
) -> None:
    """После успешного turn'а в БД лежат две row'ы с правильными ролями и references."""
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_owner_person(session_factory, owner=owner)
    assert anchor is not None

    stub = _StubAnthropicClient(deltas=["She is ", "Olga."])
    _install_stub(app, stub)
    try:
        status_code, frames, raw_body = await _post_and_collect_sse(
            app_client,
            f"/trees/{tree.id}/chat/turn",
            json_body={
                "session_id": None,
                "message": "my wife",
                "anchor_person_id": str(anchor.id),
            },
            headers=_hdr(owner),
        )
    finally:
        _clear_stub(app)

    assert status_code == 200, raw_body
    session_id = uuid.UUID(frames[0]["session_id"])

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert rows[0].role == "user"
        assert rows[0].content == "my wife"
        assert rows[1].role == "assistant"
        # Полный текст assistant'а собрался из деltas.
        assert rows[1].content == "She is Olga."
        # User-side references_jsonb — list (мб пустой если резолвер не
        # справился, мб с одним hit'ом для "my wife").
        assert isinstance(rows[0].references, list)
        # Assistant-side всегда [] в Phase 10.7c.
        assert rows[1].references == []
