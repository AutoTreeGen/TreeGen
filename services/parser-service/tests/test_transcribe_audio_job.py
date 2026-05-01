"""Phase 10.9a — integration tests для arq job ``transcribe_audio_session``.

Покрытие:

* Happy path: ``uploaded → transcribing → ready`` + transcript заполнен.
* Whisper error → ``status=failed`` + error_message с категорией.
* ``AI_DRY_RUN=true`` без api_key → mock-transcript сохраняется.
* Non-``uploaded`` status → no-op (idempotency).
* Soft-deleted сессия → no-op.

Маркеры: ``db`` + ``integration`` (testcontainers Postgres).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from ai_layer.use_cases.transcribe_audio import (
    AudioTranscriber,
    TranscribeAudioInput,
    TranscribeAudioOutput,
)
from shared_models.orm import AudioSession, Tree, User
from shared_models.storage import InMemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> AsyncIterator[Any]:
    """Async session factory привязанный к testcontainer Postgres."""
    import os

    os.environ["PARSER_SERVICE_DATABASE_URL"] = postgres_dsn
    from parser_service.database import dispose_engine, init_engine

    init_engine(postgres_dsn)
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()
    await dispose_engine()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorage:
    """Monkey-patch ``get_audio_storage`` чтобы worker использовал свежий InMemory.

    Патч идёт в namespace job-модуля (а не source-модуля), потому что
    ``transcribe_audio.py`` делает ``from ... import get_audio_storage`` —
    рефренс копируется на момент импорта, замена в source-модуле уже
    не подхватывается.
    """
    from parser_service.jobs import transcribe_audio as job_mod
    from parser_service.services import audio_storage as audio_storage_mod

    inst = InMemoryStorage()

    def _factory(*_args: Any, **_kwargs: Any) -> InMemoryStorage:
        return inst

    monkeypatch.setattr(job_mod, "get_audio_storage", _factory)
    monkeypatch.setattr(audio_storage_mod, "get_audio_storage", _factory)
    return inst


async def _make_user_tree(factory: Any) -> tuple[User, Tree]:
    async with factory() as session:
        user = User(
            email=f"voice-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:voice-{uuid.uuid4().hex[:8]}",
            display_name="Voice Owner",
            locale="en",
        )
        session.add(user)
        await session.flush()
        tree = Tree(
            owner_user_id=user.id,
            name="Voice Tree",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
            audio_consent_egress_at=dt.datetime.now(dt.UTC),
            audio_consent_egress_provider="openai",
        )
        session.add(tree)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tree)
        return user, tree


async def _make_session(
    factory: Any,
    *,
    user: User,
    tree: Tree,
    status_value: str = "uploaded",
    audio_bytes: bytes = b"\x1a\x45\xdf\xa3test",
    storage_inst: InMemoryStorage | None = None,
) -> AudioSession:
    """Создать AudioSession + положить blob в storage по ожидаемому ключу."""
    async with factory() as session:
        row = AudioSession(
            tree_id=tree.id,
            owner_user_id=user.id,
            storage_uri="s3://test/sessions/.webm",
            mime_type="audio/webm",
            size_bytes=len(audio_bytes),
            status=status_value,
            consent_egress_at=tree.audio_consent_egress_at or dt.datetime.now(dt.UTC),
            consent_egress_provider="openai",
            provenance={},
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    if storage_inst is not None:
        from parser_service.services.audio_storage import audio_object_key

        await storage_inst.put(
            audio_object_key(row.id, row.mime_type),
            audio_bytes,
            content_type=row.mime_type,
        )
    return row


def _patch_transcriber(monkeypatch: pytest.MonkeyPatch, *, output: TranscribeAudioOutput) -> None:
    """Подменить ``_build_transcriber`` на factory с заданным output'ом."""
    from parser_service.jobs import transcribe_audio as job_mod

    class _StubTranscriber:
        async def run(
            self,
            input_: TranscribeAudioInput,
            *,
            redis: Any = None,
            user_id: Any = None,
            request_id: Any = None,
        ) -> TranscribeAudioOutput:
            _ = (input_, redis, user_id, request_id)
            return output

    def _factory(*_args: Any, **_kwargs: Any) -> Any:
        return _StubTranscriber()

    monkeypatch.setattr(job_mod, "_build_transcriber", _factory)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_uploaded_to_ready(
    session_factory: Any,
    storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uploaded → transcribing → ready + transcript заполнен."""
    from parser_service.jobs.transcribe_audio import transcribe_audio_session

    user, tree = await _make_user_tree(session_factory)
    row = await _make_session(session_factory, user=user, tree=tree, storage_inst=storage)

    _patch_transcriber(
        monkeypatch,
        output=TranscribeAudioOutput(
            transcript="hello world",
            language="en",
            duration_sec=2.5,
            provider="openai-whisper-1",
            model_version="whisper-1",
            # Numeric(10, 4) на DB-уровне — выбираем cost кратный 0.0001
            # чтобы избежать round-trip-rounding в assert'ах.
            cost_usd=Decimal("0.0150"),
            error=None,
        ),
    )

    result = await transcribe_audio_session({"redis": None}, str(row.id))

    assert result["status"] == "ready"
    assert result["error"] is None

    # DB row обновлён.
    async with session_factory() as session:
        updated = await session.get(AudioSession, row.id)
        assert updated is not None
        assert updated.status == "ready"
        assert updated.transcript_text == "hello world"
        assert updated.language == "en"
        assert updated.duration_sec == pytest.approx(2.5)
        assert updated.transcript_provider == "openai-whisper-1"
        assert updated.transcript_model_version == "whisper-1"
        assert updated.transcript_cost_usd == Decimal("0.0150")
        assert updated.error_message is None


@pytest.mark.asyncio
async def test_whisper_failure_marks_failed(
    session_factory: Any,
    storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft-fail из AudioTranscriber → status=failed, error_message с категорией."""
    from parser_service.jobs.transcribe_audio import transcribe_audio_session

    user, tree = await _make_user_tree(session_factory)
    row = await _make_session(session_factory, user=user, tree=tree, storage_inst=storage)

    _patch_transcriber(
        monkeypatch,
        output=TranscribeAudioOutput(
            transcript="",
            language=None,
            duration_sec=None,
            provider="openai-whisper-1",
            model_version="whisper-1",
            cost_usd=Decimal("0"),
            error="api:Whisper API failed after retry",
        ),
    )

    result = await transcribe_audio_session({"redis": None}, str(row.id))

    assert result["status"] == "failed"
    assert "api:Whisper API failed" in result["error"]

    async with session_factory() as session:
        updated = await session.get(AudioSession, row.id)
        assert updated.status == "failed"
        assert updated.transcript_text is None
        assert updated.error_message is not None
        assert updated.error_message.startswith("api:")


@pytest.mark.asyncio
async def test_dry_run_path_uses_real_transcriber(
    session_factory: Any,
    storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AI_DRY_RUN=true`` без api_key — реальный AudioTranscriber возвращает mock."""
    from parser_service.jobs.transcribe_audio import transcribe_audio_session

    monkeypatch.setenv("AI_DRY_RUN", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    user, tree = await _make_user_tree(session_factory)
    row = await _make_session(session_factory, user=user, tree=tree, storage_inst=storage)

    # Сбросить settings-cache: parser_service.config.get_settings создаёт
    # новые Settings() на каждый вызов (без lru_cache), значит pickup'нет
    # свежий env-flag.
    result = await transcribe_audio_session({"redis": None}, str(row.id))
    assert result["status"] == "ready"

    async with session_factory() as session:
        updated = await session.get(AudioSession, row.id)
        assert updated.status == "ready"
        # Mock-транскрипт от WhisperClient._dry_run_result.
        assert updated.transcript_text == "[dry-run mock RU]"


@pytest.mark.asyncio
async def test_non_uploaded_status_is_noop(
    session_factory: Any,
    storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сессия в статусе ``ready`` → no-op (idempotency)."""
    from parser_service.jobs.transcribe_audio import transcribe_audio_session

    user, tree = await _make_user_tree(session_factory)
    row = await _make_session(
        session_factory,
        user=user,
        tree=tree,
        status_value="ready",
        storage_inst=storage,
    )

    # Should NOT need to call transcriber.
    _patch_transcriber(
        monkeypatch,
        output=TranscribeAudioOutput(
            transcript="should-not-overwrite",
            language=None,
            duration_sec=None,
            provider="openai-whisper-1",
            model_version="whisper-1",
            cost_usd=Decimal("0"),
            error=None,
        ),
    )

    result = await transcribe_audio_session({"redis": None}, str(row.id))
    assert result.get("skipped") == "non_uploaded_status"

    async with session_factory() as session:
        updated = await session.get(AudioSession, row.id)
        assert updated.status == "ready"
        # transcript не перезаписан.
        assert updated.transcript_text != "should-not-overwrite"


@pytest.mark.asyncio
async def test_soft_deleted_session_skipped(
    session_factory: Any,
    storage: InMemoryStorage,
) -> None:
    """``deleted_at IS NOT NULL`` → no-op (erasure-job уже сработал)."""
    from parser_service.jobs.transcribe_audio import transcribe_audio_session

    user, tree = await _make_user_tree(session_factory)
    row = await _make_session(session_factory, user=user, tree=tree, storage_inst=storage)
    async with session_factory() as session:
        sess_row = await session.get(AudioSession, row.id)
        sess_row.deleted_at = dt.datetime.now(dt.UTC)
        await session.commit()

    result = await transcribe_audio_session({"redis": None}, str(row.id))
    assert result.get("skipped") == "deleted"


# AudioTranscriber re-exported here so test reads as «patches the use case»
# explicitly even though we substitute a stub.
_ = AudioTranscriber
