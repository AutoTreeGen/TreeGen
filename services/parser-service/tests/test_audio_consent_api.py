"""Phase 10.9a — integration tests для audio-consent API (ADR-0064 §B1).

Проверки:

* GET без consent → 200 с null fields.
* POST as EDITOR (не OWNER) → 403.
* POST as OWNER → 200 set + idempotent повтор → 200 с тем же timestamp.
* DELETE → 202, enqueue erasure-job для каждой неудалённой сессии.

Auth — через ``X-User-Id`` header (см. conftest ``_fake_current_user_override``).
arq pool — auto-overridden в conftest (``_override_arq_pool`` fixture).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import (
    AudioSession,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"voice-{uuid.uuid4().hex[:8]}@example.com"
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


async def _make_tree_with_owner(factory: Any, *, owner: User) -> Tree:
    """Tree + OWNER membership-row (mirror sharing.test_sharing helper)."""
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Voice Test {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        m = TreeMembership(
            tree_id=tree.id,
            user_id=owner.id,
            role=TreeRole.OWNER.value,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(m)
        await session.commit()
        await session.refresh(tree)
        return tree


async def _add_membership(factory: Any, *, tree: Tree, user: User, role: TreeRole) -> None:
    async with factory() as session:
        session.add(
            TreeMembership(
                tree_id=tree.id,
                user_id=user.id,
                role=role.value,
                accepted_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# GET /trees/{id}/audio-consent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_consent_returns_null_when_not_granted(app_client, session_factory: Any) -> None:
    """Свежее дерево — оба поля null."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    r = await app_client.get(f"/trees/{tree.id}/audio-consent", headers=_hdr(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tree_id"] == str(tree.id)
    assert body["audio_consent_egress_at"] is None
    assert body["audio_consent_egress_provider"] is None


@pytest.mark.asyncio
async def test_get_consent_viewer_allowed(app_client, session_factory: Any) -> None:
    """VIEWER может читать consent — это read-level info."""
    owner = await _make_user(session_factory)
    viewer = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_membership(session_factory, tree=tree, user=viewer, role=TreeRole.VIEWER)

    r = await app_client.get(f"/trees/{tree.id}/audio-consent", headers=_hdr(viewer))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /trees/{id}/audio-consent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_consent_editor_forbidden(app_client, session_factory: Any) -> None:
    """EDITOR не может set'ить consent — это OWNER-only решение."""
    owner = await _make_user(session_factory)
    editor = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_membership(session_factory, tree=tree, user=editor, role=TreeRole.EDITOR)

    r = await app_client.post(
        f"/trees/{tree.id}/audio-consent",
        json={"provider": "openai"},
        headers=_hdr(editor),
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_post_consent_owner_grants(app_client, session_factory: Any) -> None:
    """OWNER set'ит consent → 200 с timestamp + provider."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/audio-consent",
        json={"provider": "openai"},
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tree_id"] == str(tree.id)
    assert body["audio_consent_egress_at"] is not None
    assert body["audio_consent_egress_provider"] == "openai"


@pytest.mark.asyncio
async def test_post_consent_idempotent(app_client, session_factory: Any) -> None:
    """Повторный POST — тот же timestamp (не перезаписываем)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    r1 = await app_client.post(
        f"/trees/{tree.id}/audio-consent",
        json={"provider": "openai"},
        headers=_hdr(owner),
    )
    assert r1.status_code == 200
    ts1 = r1.json()["audio_consent_egress_at"]

    r2 = await app_client.post(
        f"/trees/{tree.id}/audio-consent",
        json={"provider": "openai"},
        headers=_hdr(owner),
    )
    assert r2.status_code == 200
    assert r2.json()["audio_consent_egress_at"] == ts1


@pytest.mark.asyncio
async def test_post_consent_provider_mismatch_conflict(app_client, session_factory: Any) -> None:
    """Смена провайдера на already-granted consent → 409 (revoke first)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    await app_client.post(
        f"/trees/{tree.id}/audio-consent",
        json={"provider": "openai"},
        headers=_hdr(owner),
    )

    r = await app_client.post(
        f"/trees/{tree.id}/audio-consent",
        json={"provider": "self-hosted-whisper"},
        headers=_hdr(owner),
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /trees/{id}/audio-consent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_consent_revokes_and_enqueues(app_client, session_factory: Any) -> None:
    """DELETE → 202, поля сброшены в null, erasure-job enqueue'нут на каждую сессию."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    # Пишем consent + 2 неудалённых session'а напрямую в БД (не через POST,
    # потому что этому тесту нужно только revoke flow проверить).
    now = dt.datetime.now(dt.UTC)
    async with session_factory() as session:
        tree_row = await session.get(Tree, tree.id)
        tree_row.audio_consent_egress_at = now
        tree_row.audio_consent_egress_provider = "openai"
        for i in range(2):
            session.add(
                AudioSession(
                    tree_id=tree.id,
                    owner_user_id=owner.id,
                    storage_uri=f"s3://test/sessions/{i}.webm",
                    mime_type="audio/webm",
                    size_bytes=1024,
                    status="ready",
                    consent_egress_at=now,
                    consent_egress_provider="openai",
                    provenance={},
                )
            )
        await session.commit()

    # Подсмотрим pool-mock из app.dependency_overrides, чтобы посчитать enqueue.
    from parser_service.queue import get_arq_pool

    fake_pool = app_client._transport.app.dependency_overrides[get_arq_pool]()

    r = await app_client.delete(
        f"/trees/{tree.id}/audio-consent",
        headers=_hdr(owner),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["tree_id"] == str(tree.id)
    assert body["revoked_at"] is not None
    assert len(body["enqueued_session_ids"]) == 2

    # Поля consent на дереве сброшены.
    async with session_factory() as session:
        tree_row = await session.get(Tree, tree.id)
        assert tree_row.audio_consent_egress_at is None
        assert tree_row.audio_consent_egress_provider is None

    # Pool.enqueue_job вызван 2 раза с erase_audio_session.
    enqueue_calls = fake_pool.enqueue_job.call_args_list
    assert len(enqueue_calls) >= 2, f"expected >= 2 enqueue calls, got {enqueue_calls}"
    erase_calls = [c for c in enqueue_calls if c.args and c.args[0] == "erase_audio_session"]
    assert len(erase_calls) == 2


@pytest.mark.asyncio
async def test_delete_consent_no_sessions_empty_list(app_client, session_factory: Any) -> None:
    """DELETE на дереве без сессий → 202 с пустым списком."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    # Set consent first.
    await app_client.post(
        f"/trees/{tree.id}/audio-consent",
        json={"provider": "openai"},
        headers=_hdr(owner),
    )

    r = await app_client.delete(
        f"/trees/{tree.id}/audio-consent",
        headers=_hdr(owner),
    )
    assert r.status_code == 202
    assert r.json()["enqueued_session_ids"] == []


@pytest.mark.asyncio
async def test_delete_consent_editor_forbidden(app_client, session_factory: Any) -> None:
    """EDITOR не может revoke consent — OWNER-only."""
    owner = await _make_user(session_factory)
    editor = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _add_membership(session_factory, tree=tree, user=editor, role=TreeRole.EDITOR)

    r = await app_client.delete(
        f"/trees/{tree.id}/audio-consent",
        headers=_hdr(editor),
    )
    assert r.status_code == 403
