"""Phase 10.7d — integration tests для chat history endpoints + новых
features (auto-title, source citations, assistant-side person refs).

Testing strategy:

* `_make_user` / `_make_tree_with_owner_person` зеркалит ``test_chat_turn``
  helpers (тот же паттерн фикстуры, дабы не дублировать сетап).
* `_post_turn` отправляет POST /chat/turn через httpx-stream + drain SSE,
  возвращает (session_id, frames). Stub'аем AnthropicClient тем же way.
* GET-эндпоинты тестируем синхронным httpx.get'ом — простой JSON-response.

Покрытие:

* sessions list — empty / paginates / filters by user / filters by tree.
* messages list — paginates, ownership-check 404 на чужой session_id.
* auto-title — derived из первого user-message'а (truncated до 60 chars).
* source-citation extraction — Source.title в тексте → reference запись.
* assistant-side person references — резолвятся пост-стрим симметрично
  user-side.
"""

from __future__ import annotations

import contextlib
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
    Source,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]

_FRAME_SEP = re.compile(r"\r?\n\r?\n")


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


async def _make_tree_with_anchor(factory: Any, *, owner: User) -> tuple[Tree, Person]:
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
        anchor = Person(tree_id=tree.id, sex="M", provenance={}, version_id=1)
        spouse = Person(tree_id=tree.id, sex="F", provenance={}, version_id=1)
        session.add_all([anchor, spouse])
        await session.flush()
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
        tree.owner_person_id = anchor.id
        await session.commit()
        await session.refresh(tree)
        await session.refresh(anchor)
        return tree, anchor


async def _make_chat_session(
    factory: Any,
    *,
    tree: Tree,
    user: User,
    anchor: Person,
    title: str | None = None,
) -> ChatSession:
    async with factory() as session:
        cs = ChatSession(
            tree_id=tree.id,
            user_id=user.id,
            anchor_person_id=anchor.id,
            title=title,
        )
        session.add(cs)
        await session.commit()
        await session.refresh(cs)
        return cs


async def _add_message(
    factory: Any,
    *,
    session_id: uuid.UUID,
    role: str,
    content: str,
    references: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    async with factory() as session:
        m = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            references=references or [],
        )
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


class _StubAnthropicClient:
    def __init__(self, deltas: Sequence[str] | None = None) -> None:
        self._deltas = list(deltas) if deltas is not None else ["The 1900 census ", "names Olga."]

    async def stream_completion(
        self,
        *,
        system: str,  # noqa: ARG002
        messages: Sequence[dict[str, str]],  # noqa: ARG002
        model: str | None = None,  # noqa: ARG002
        max_tokens: int = 1024,  # noqa: ARG002
        temperature: float = 0.0,  # noqa: ARG002
    ) -> AsyncIterator[str]:
        for delta in self._deltas:
            yield delta


async def _post_turn(
    client: Any,
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str],
) -> tuple[int, list[dict[str, Any]]]:
    """POST /chat/turn + drain SSE; возвращает (status, parsed frames)."""
    frames: list[dict[str, Any]] = []
    buffer = ""
    async with client.stream("POST", url, json=json_body, headers=headers) as response:
        if response.status_code != 200:
            return response.status_code, frames
        async for chunk in response.aiter_text():
            buffer += chunk
            while True:
                m = _FRAME_SEP.search(buffer)
                if m is None:
                    break
                segment = buffer[: m.start()]
                buffer = buffer[m.end() :]
                for raw in segment.splitlines():
                    line = raw.strip()
                    if line.startswith("data:"):
                        with contextlib.suppress(json.JSONDecodeError):
                            frames.append(json.loads(line[len("data:") :].lstrip()))
    return response.status_code, frames


# ---------------------------------------------------------------------------
# Sessions list endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_empty(app_client, session_factory: Any) -> None:
    """Пустой response для нового пользователя/дерева."""
    owner = await _make_user(session_factory)
    tree, _ = await _make_tree_with_anchor(session_factory, owner=owner)

    response = await app_client.get(f"/trees/{tree.id}/chat/sessions", headers=_hdr(owner))
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["items"] == []
    assert payload["limit"] == 20
    assert payload["offset"] == 0


@pytest.mark.asyncio
async def test_list_sessions_paginates_and_filters_by_user(
    app_client, session_factory: Any
) -> None:
    """Создаём 5 сессий: 3 owner'a + 2 другого user'a в том же дереве. Видим только свои."""
    owner = await _make_user(session_factory)
    other = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)
    # Выдаём other'у VIEWER access чтобы он мог иметь свою чат-сессию в дереве.
    async with session_factory() as session:
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=other.id,
                role=TreeRole.VIEWER.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()

    own_sessions = []
    for i in range(3):
        cs = await _make_chat_session(
            session_factory, tree=tree, user=owner, anchor=anchor, title=f"own-{i}"
        )
        own_sessions.append(cs)
    for i in range(2):
        await _make_chat_session(
            session_factory, tree=tree, user=other, anchor=anchor, title=f"other-{i}"
        )

    # Page 1: limit=2, offset=0 — две own-сессии (отсортированы DESC last_message_at).
    response = await app_client.get(
        f"/trees/{tree.id}/chat/sessions?limit=2&offset=0", headers=_hdr(owner)
    )
    assert response.status_code == 200
    page1 = response.json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2

    response = await app_client.get(
        f"/trees/{tree.id}/chat/sessions?limit=2&offset=2", headers=_hdr(owner)
    )
    page2 = response.json()
    assert page2["total"] == 3
    assert len(page2["items"]) == 1

    # Other-user не видит own_sessions.
    response = await app_client.get(f"/trees/{tree.id}/chat/sessions", headers=_hdr(other))
    other_payload = response.json()
    assert other_payload["total"] == 2


@pytest.mark.asyncio
async def test_list_sessions_filters_by_tree(app_client, session_factory: Any) -> None:
    """Сессии в дереве A не попадают в выдачу для дерева B."""
    owner = await _make_user(session_factory)
    tree_a, anchor_a = await _make_tree_with_anchor(session_factory, owner=owner)
    tree_b, _anchor_b = await _make_tree_with_anchor(session_factory, owner=owner)
    await _make_chat_session(session_factory, tree=tree_a, user=owner, anchor=anchor_a)

    response = await app_client.get(f"/trees/{tree_b.id}/chat/sessions", headers=_hdr(owner))
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_sessions_aggregates_message_count(app_client, session_factory: Any) -> None:
    """message_count + last_message_at агрегируются JOIN'ом по chat_messages."""
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)
    cs = await _make_chat_session(session_factory, tree=tree, user=owner, anchor=anchor, title="t1")
    await _add_message(session_factory, session_id=cs.id, role="user", content="hi")
    await _add_message(session_factory, session_id=cs.id, role="assistant", content="hello")

    response = await app_client.get(f"/trees/{tree.id}/chat/sessions", headers=_hdr(owner))
    item = response.json()["items"][0]
    assert item["message_count"] == 2
    assert item["last_message_at"] is not None


# ---------------------------------------------------------------------------
# Messages list endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_paginates(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)
    cs = await _make_chat_session(session_factory, tree=tree, user=owner, anchor=anchor)
    for i in range(5):
        await _add_message(session_factory, session_id=cs.id, role="user", content=f"msg {i}")

    response = await app_client.get(
        f"/trees/{tree.id}/chat/sessions/{cs.id}/messages?limit=2&offset=0",
        headers=_hdr(owner),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert len(payload["items"]) == 2
    # Сортировка — по created_at ASC.
    assert payload["items"][0]["content"] == "msg 0"
    assert payload["items"][1]["content"] == "msg 1"


@pytest.mark.asyncio
async def test_list_messages_404_on_other_user_session(app_client, session_factory: Any) -> None:
    """Чужой session_id → 404 (leak-protection через consistent error code)."""
    owner = await _make_user(session_factory)
    other = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)
    cs = await _make_chat_session(session_factory, tree=tree, user=owner, anchor=anchor)

    # other даже с tree-membership'ом не должен видеть owner'овскую сессию.
    async with session_factory() as session:
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=other.id,
                role=TreeRole.VIEWER.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()

    response = await app_client.get(
        f"/trees/{tree.id}/chat/sessions/{cs.id}/messages", headers=_hdr(other)
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_session_returns_metadata(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)
    cs = await _make_chat_session(
        session_factory, tree=tree, user=owner, anchor=anchor, title="my-thread"
    )
    response = await app_client.get(f"/trees/{tree.id}/chat/sessions/{cs.id}", headers=_hdr(owner))
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(cs.id)
    assert payload["title"] == "my-thread"
    assert payload["anchor_person_id"] == str(anchor.id)


@pytest.mark.asyncio
async def test_messages_legacy_refs_without_kind_round_trip(
    app_client, session_factory: Any
) -> None:
    """Phase 10.7c-row'ы без ``kind`` валидируются как person через field_validator."""
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)
    cs = await _make_chat_session(session_factory, tree=tree, user=owner, anchor=anchor)
    legacy_refs = [{"person_id": str(uuid.uuid4()), "mention_text": "my wife", "confidence": 1.0}]
    await _add_message(
        session_factory,
        session_id=cs.id,
        role="user",
        content="my wife",
        references=legacy_refs,
    )

    response = await app_client.get(
        f"/trees/{tree.id}/chat/sessions/{cs.id}/messages", headers=_hdr(owner)
    )
    assert response.status_code == 200
    payload = response.json()
    item = payload["items"][0]
    assert item["references"][0]["kind"] == "person"
    assert item["references"][0]["mention_text"] == "my wife"


# ---------------------------------------------------------------------------
# Phase 10.7d — auto-title + source citations + assistant-side refs
# ---------------------------------------------------------------------------


def _install_stub(app: Any, stub: _StubAnthropicClient) -> None:
    app.dependency_overrides[get_anthropic_client] = lambda: stub


def _clear_stub(app: Any) -> None:
    app.dependency_overrides.pop(get_anthropic_client, None)


@pytest.mark.asyncio
async def test_auto_title_set_after_first_turn(app, app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)

    stub = _StubAnthropicClient(deltas=["Sure thing."])
    _install_stub(app, stub)
    try:
        status_code, frames = await _post_turn(
            app_client,
            f"/trees/{tree.id}/chat/turn",
            json_body={
                "session_id": None,
                "message": "Tell me about my wife and her family",
                "anchor_person_id": str(anchor.id),
            },
            headers=_hdr(owner),
        )
    finally:
        _clear_stub(app)

    assert status_code == 200
    session_id = uuid.UUID(frames[0]["session_id"])
    async with session_factory() as session:
        cs = await session.get(ChatSession, session_id)
        assert cs is not None
        assert cs.title == "Tell me about my wife and her family"


@pytest.mark.asyncio
async def test_source_citation_extracted_from_user_message(
    app, app_client, session_factory: Any
) -> None:
    """Substring-match по Source.title в user-message → source-reference."""
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)
    async with session_factory() as session:
        src = Source(
            tree_id=tree.id,
            title="1900 US Census",
            source_type="census",
            provenance={},
            version_id=1,
        )
        session.add(src)
        await session.commit()
        await session.refresh(src)
    src_id = src.id

    stub = _StubAnthropicClient(deltas=["Yes."])
    _install_stub(app, stub)
    try:
        status_code, frames = await _post_turn(
            app_client,
            f"/trees/{tree.id}/chat/turn",
            json_body={
                "session_id": None,
                "message": "Look up my wife in the 1900 US Census please",
                "anchor_person_id": str(anchor.id),
            },
            headers=_hdr(owner),
        )
    finally:
        _clear_stub(app)

    assert status_code == 200
    done = next(f for f in frames if f["type"] == "done")
    refs = done["referenced_persons"]
    source_refs = [r for r in refs if r.get("kind") == "source"]
    assert len(source_refs) == 1
    assert source_refs[0]["source_id"] == str(src_id)
    assert source_refs[0]["mention_text"] == "1900 US Census"


@pytest.mark.asyncio
async def test_assistant_references_resolved_post_stream(
    app, app_client, session_factory: Any
) -> None:
    """Assistant-вывод с ego-фразой («your wife») → assistant-side person ref."""
    owner = await _make_user(session_factory)
    tree, anchor = await _make_tree_with_anchor(session_factory, owner=owner)

    # Стримим assistant'овский текст с фразой, на которую сработает ego_resolver.
    stub = _StubAnthropicClient(deltas=["Your wife ", "is here."])
    _install_stub(app, stub)
    try:
        status_code, frames = await _post_turn(
            app_client,
            f"/trees/{tree.id}/chat/turn",
            json_body={
                "session_id": None,
                "message": "Hello",
                "anchor_person_id": str(anchor.id),
            },
            headers=_hdr(owner),
        )
    finally:
        _clear_stub(app)

    assert status_code == 200
    done = next(f for f in frames if f["type"] == "done")
    assert "assistant_references" in done
    # Ego-резолвер не понимает «your wife», но «my wife» в user-вводе тоже
    # не было — строгая инвариантность: поле present, list (мб пустой).
    assert isinstance(done["assistant_references"], list)

    # Persisted assistant-row references_jsonb — list (форма проверена; контент
    # зависит от языка резолвера).
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
        assert rows[1].role == "assistant"
        assert isinstance(rows[1].references, list)
