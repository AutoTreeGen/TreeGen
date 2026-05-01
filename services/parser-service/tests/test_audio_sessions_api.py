"""Phase 10.9a — integration tests для audio-sessions API (ADR-0064 §3.3).

**Critical CI-blocking test:** ``test_post_without_consent_returns_403`` —
без consent'а POST НИКОГДА не должен принимать аудио. Это последняя линия
privacy-gate'а на API-уровне (поверх UI-disabled-кнопки и DB
``NOT NULL consent_egress_at``).

Маркеры: ``db`` + ``integration`` (testcontainers Postgres).
"""

from __future__ import annotations

import datetime as dt
import io
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
from shared_models.storage import InMemoryStorage
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


@pytest.fixture
def audio_storage(app):
    """Override ``get_audio_storage`` свежей :class:`InMemoryStorage` per test.

    Без override singleton в ``services/audio_storage.py`` шарится между
    тестами; для CI-determinism хотим pristine state на каждый тест.
    """
    from parser_service.services.audio_storage import (
        get_audio_storage,
        reset_audio_storage_cache,
    )

    storage = InMemoryStorage()
    app.dependency_overrides[get_audio_storage] = lambda: storage
    reset_audio_storage_cache()
    yield storage
    app.dependency_overrides.pop(get_audio_storage, None)
    reset_audio_storage_cache()


@pytest.fixture
def stt_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включить ``AI_DRY_RUN=true`` чтобы POST не падал в 503 на отсутствующем ключе.

    Тесты STT-availability проверяют 503-путь явно (см.
    ``test_post_without_openai_key_returns_503``).
    """
    monkeypatch.setenv("AI_DRY_RUN", "true")
    # Cache settings reset не нужен: Settings конструируется заново на
    # каждый Depends call (см. ``parser_service.config.get_settings``).


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


async def _grant_consent(factory: Any, *, tree: Tree) -> None:
    """Выставить consent-поля прямо на ``Tree`` row (минуя POST endpoint)."""
    async with factory() as session:
        row = await session.get(Tree, tree.id)
        row.audio_consent_egress_at = dt.datetime.now(dt.UTC)
        row.audio_consent_egress_provider = "openai"
        await session.commit()


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


def _audio_files(*, mime: str = "audio/webm") -> dict[str, Any]:
    """Tiny multipart payload для httpx-AsyncClient.post(..., files=...)."""
    return {"audio": ("clip.webm", io.BytesIO(b"\x1a\x45\xdf\xa3test-audio-bytes"), mime)}


# ---------------------------------------------------------------------------
# CRITICAL — consent gate (CI-blocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("audio_storage", "stt_configured")
async def test_post_without_consent_returns_403(app_client, session_factory: Any) -> None:
    """**CRITICAL CI-блок.** POST без consent → 403 ``consent_required``.

    Это последний слой privacy-gate'а на API-уровне; UI имеет disabled-кнопку,
    DB имеет NOT NULL constraint. Этот тест проверяет, что middle layer
    тоже отказывает — defence-in-depth.
    """
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    r = await app_client.post(
        f"/trees/{tree.id}/audio-sessions",
        files=_audio_files(),
        headers=_hdr(owner),
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "consent_required"
    assert detail["tree_id"] == str(tree.id)


@pytest.mark.asyncio
@pytest.mark.usefixtures("stt_configured")
async def test_post_with_consent_creates_session_and_enqueues(
    app_client, session_factory: Any, audio_storage: InMemoryStorage
) -> None:
    """С consent'ом — 201, blob в storage, arq job enqueue'нут."""
    from parser_service.queue import get_arq_pool

    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _grant_consent(session_factory, tree=tree)

    fake_pool = app_client._transport.app.dependency_overrides[get_arq_pool]()

    r = await app_client.post(
        f"/trees/{tree.id}/audio-sessions",
        files=_audio_files(),
        headers=_hdr(owner),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["tree_id"] == str(tree.id)
    assert body["status"] == "uploaded"
    assert body["mime_type"] == "audio/webm"
    assert body["size_bytes"] > 0
    session_id = body["id"]

    # Blob в storage.
    expected_key = f"sessions/{session_id}.webm"
    assert await audio_storage.exists(expected_key)

    # Job enqueue'нут с правильным name.
    enqueue_calls = fake_pool.enqueue_job.call_args_list
    transcribe_calls = [
        c for c in enqueue_calls if c.args and c.args[0] == "transcribe_audio_session"
    ]
    assert len(transcribe_calls) == 1
    assert transcribe_calls[0].args[1] == session_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("audio_storage", "stt_configured")
async def test_post_unsupported_mime_returns_415(app_client, session_factory: Any) -> None:
    """MIME вне allowlist → 415, никаких side-effects."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _grant_consent(session_factory, tree=tree)

    r = await app_client.post(
        f"/trees/{tree.id}/audio-sessions",
        files={"audio": ("clip.bin", io.BytesIO(b"random"), "application/octet-stream")},
        headers=_hdr(owner),
    )
    assert r.status_code == 415, r.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("audio_storage", "stt_configured")
async def test_post_too_large_returns_413(
    app_client,
    session_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Превышение ``AUDIO_MAX_SIZE_BYTES`` → 413."""
    monkeypatch.setenv("AUDIO_MAX_SIZE_BYTES", "100")  # 100 байт лимит для теста

    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _grant_consent(session_factory, tree=tree)

    big = io.BytesIO(b"\x00" * 200)
    r = await app_client.post(
        f"/trees/{tree.id}/audio-sessions",
        files={"audio": ("big.webm", big, "audio/webm")},
        headers=_hdr(owner),
    )
    assert r.status_code == 413, r.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("audio_storage")
async def test_post_without_openai_key_returns_503(
    app_client,
    session_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет ``OPENAI_API_KEY`` и ``AI_DRY_RUN=false`` → 503 ``stt_unavailable``."""
    # Default for AI_DRY_RUN — false; OPENAI_API_KEY не выставлен в test env.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_DRY_RUN", "false")

    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _grant_consent(session_factory, tree=tree)

    r = await app_client.post(
        f"/trees/{tree.id}/audio-sessions",
        files=_audio_files(),
        headers=_hdr(owner),
    )
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["error_code"] == "stt_unavailable"


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("audio_storage", "stt_configured")
async def test_list_sessions_empty(app_client, session_factory: Any) -> None:
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)

    r = await app_client.get(f"/trees/{tree.id}/audio-sessions", headers=_hdr(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("audio_storage", "stt_configured")
async def test_get_session_returns_transcript(app_client, session_factory: Any) -> None:
    """GET single — возвращает transcript_text (если уже выставлен)."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _grant_consent(session_factory, tree=tree)

    # Создаём ready-сессию напрямую (без worker'а).
    async with session_factory() as session:
        row = AudioSession(
            tree_id=tree.id,
            owner_user_id=owner.id,
            storage_uri="s3://test/sessions/abc.webm",
            mime_type="audio/webm",
            size_bytes=2048,
            status="ready",
            transcript_text="hello world",
            transcript_provider="openai-whisper-1",
            transcript_model_version="whisper-1",
            language="en",
            consent_egress_at=dt.datetime.now(dt.UTC),
            consent_egress_provider="openai",
            provenance={},
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        session_id = row.id

    r = await app_client.get(f"/audio-sessions/{session_id}", headers=_hdr(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transcript_text"] == "hello world"
    assert body["transcript_provider"] == "openai-whisper-1"
    assert body["language"] == "en"
    assert body["status"] == "ready"


@pytest.mark.asyncio
@pytest.mark.usefixtures("audio_storage", "stt_configured")
async def test_delete_session_soft_deletes(app_client, session_factory: Any) -> None:
    """DELETE проставляет ``deleted_at``; GET снова показывает row с deleted_at != null."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    await _grant_consent(session_factory, tree=tree)

    async with session_factory() as session:
        row = AudioSession(
            tree_id=tree.id,
            owner_user_id=owner.id,
            storage_uri="s3://test/sessions/abc.webm",
            mime_type="audio/webm",
            size_bytes=512,
            status="ready",
            consent_egress_at=dt.datetime.now(dt.UTC),
            consent_egress_provider="openai",
            provenance={},
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        session_id = row.id

    r = await app_client.delete(f"/audio-sessions/{session_id}", headers=_hdr(owner))
    assert r.status_code == 200, r.text
    assert r.json()["deleted_at"] is not None

    # GET снова — deleted_at != null.
    g = await app_client.get(f"/audio-sessions/{session_id}", headers=_hdr(owner))
    assert g.status_code == 200
    assert g.json()["deleted_at"] is not None
