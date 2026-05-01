"""Тесты ORM ``AudioSession`` (Phase 10.9a / ADR-0064).

Покрывают:
- enum значения ``AudioSessionStatus`` (unit, без БД).
- round-trip create/query + soft-delete (integration).
- ProvenanceMixin поля: ``source_files`` / ``import_job_id`` / ``manual_edits``.
- Privacy-gate: insert без ``consent_egress_at`` падает на DB-уровне.
- ``status`` CHECK-constraint: invalid value → IntegrityError.
- ``size_bytes`` non-negative CHECK.
- FK cascade: ``DELETE FROM trees`` → audio_sessions удаляются.

Negative-кейсы используют ``session.begin_nested()`` (SAVEPOINT), чтобы
после IntegrityError транзакция fixture'а оставалась пригодной для
finalize'а — иначе outer ``session.begin()`` из conftest падает на exit.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from shared_models.orm import AudioSession, AudioSessionStatus, Tree, User
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Unit: enum (без БД)
# ---------------------------------------------------------------------------


def test_audio_session_status_values() -> None:
    """Enum покрывает 4 lifecycle-состояния и хранит string-values."""
    assert {s.value for s in AudioSessionStatus} == {
        "uploaded",
        "transcribing",
        "ready",
        "failed",
    }
    assert AudioSessionStatus.UPLOADED.value == "uploaded"


# ---------------------------------------------------------------------------
# Helpers (integration)
# ---------------------------------------------------------------------------


async def _seed_user_and_tree(session: AsyncSession) -> tuple[User, Tree]:
    """Создать пользователя и дерево как scope для audio_session."""
    user = User(
        email="voice-owner@example.com",
        external_auth_id="auth0|voice-test-1",
        display_name="Voice Owner",
    )
    session.add(user)
    await session.flush()

    tree = Tree(owner_user_id=user.id, name="Voice Tree")
    session.add(tree)
    await session.flush()
    return user, tree


def _make_session(tree: Tree, user: User, **overrides: Any) -> AudioSession:
    """Шаблонный AudioSession со всеми обязательными полями.

    Тесты переопределяют отдельные поля через kwargs — всё остальное
    остаётся валидным, чтобы не зашумлять негативные кейсы.
    """
    defaults: dict[str, Any] = {
        "tree_id": tree.id,
        "owner_user_id": user.id,
        "storage_uri": "s3://test/audio/sample.webm",
        "mime_type": "audio/webm; codecs=opus",
        "duration_sec": 12.5,
        "size_bytes": 1024,
        "consent_egress_at": dt.datetime.now(dt.UTC),
        "consent_egress_provider": "openai",
    }
    defaults.update(overrides)
    return AudioSession(**defaults)


# ---------------------------------------------------------------------------
# Integration (требует Postgres)
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_audio_session_round_trip(db_session: AsyncSession) -> None:
    """Create → query → soft-delete → query (excluded by default)."""
    user, tree = await _seed_user_and_tree(db_session)

    audio = _make_session(
        tree,
        user,
        provenance={
            "source_files": ["recording-2026-05-01.webm"],
            "import_job_id": None,
            "manual_edits": [],
        },
    )
    db_session.add(audio)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(AudioSession).where(AudioSession.id == audio.id))
    ).scalar_one()
    assert fetched.status == AudioSessionStatus.UPLOADED.value
    assert fetched.consent_egress_provider == "openai"
    assert fetched.provenance["source_files"] == ["recording-2026-05-01.webm"]
    assert fetched.is_deleted is False

    # Soft-delete: ставим deleted_at, row остаётся в таблице.
    fetched.deleted_at = dt.datetime.now(dt.UTC)
    await db_session.flush()

    after = (
        await db_session.execute(
            select(AudioSession).where(
                AudioSession.id == audio.id,
                AudioSession.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    assert after is None, "soft-deleted row не должен попадать в default-query"


@pytest.mark.db
@pytest.mark.integration
async def test_audio_session_status_transition(db_session: AsyncSession) -> None:
    """Worker-flow: uploaded → transcribing → ready с заполнением полей."""
    user, tree = await _seed_user_and_tree(db_session)

    audio = _make_session(tree, user)
    db_session.add(audio)
    await db_session.flush()

    audio.status = AudioSessionStatus.TRANSCRIBING.value
    await db_session.flush()

    audio.status = AudioSessionStatus.READY.value
    audio.transcript_text = "Прадед родился в 1875 году в Бердичеве."
    audio.language = "ru"
    audio.transcript_provider = "openai"
    audio.transcript_model_version = "whisper-1"
    audio.transcript_cost_usd = Decimal("0.0750")
    await db_session.flush()

    fetched = (
        await db_session.execute(select(AudioSession).where(AudioSession.id == audio.id))
    ).scalar_one()
    assert fetched.status == AudioSessionStatus.READY.value
    assert fetched.transcript_text is not None
    assert fetched.transcript_cost_usd == Decimal("0.0750")


@pytest.mark.db
@pytest.mark.integration
async def test_audio_session_consent_egress_at_not_null(
    db_session: AsyncSession,
) -> None:
    """Privacy-gate: insert с consent_egress_at=NULL падает на DB-уровне."""
    user, tree = await _seed_user_and_tree(db_session)

    audio = _make_session(tree, user)
    audio.consent_egress_at = None  # type: ignore[assignment]
    db_session.add(audio)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


@pytest.mark.db
@pytest.mark.integration
async def test_audio_session_status_check_constraint(
    db_session: AsyncSession,
) -> None:
    """CHECK на ``status``: значение вне enum → IntegrityError."""
    user, tree = await _seed_user_and_tree(db_session)

    audio = _make_session(tree, user, status="bogus_status")
    db_session.add(audio)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


@pytest.mark.db
@pytest.mark.integration
async def test_audio_session_negative_size_rejected(
    db_session: AsyncSession,
) -> None:
    """``size_bytes >= 0`` — отрицательный размер недопустим."""
    user, tree = await _seed_user_and_tree(db_session)

    audio = _make_session(tree, user, size_bytes=-1)
    db_session.add(audio)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


@pytest.mark.db
@pytest.mark.integration
async def test_audio_session_cascades_on_tree_delete(
    db_session: AsyncSession,
) -> None:
    """``DELETE FROM trees`` каскадно чистит audio_sessions (FK CASCADE).

    GDPR-erasure (ADR-0049) — application-level, но DB-CASCADE гарантирует,
    что прямое удаление дерева не оставляет orphan'ов в audio_sessions.
    """
    user, tree = await _seed_user_and_tree(db_session)

    audio = _make_session(tree, user)
    db_session.add(audio)
    await db_session.flush()
    audio_id = audio.id

    # Минуем ORM-каскад / audit-listener'ы и прямо стираем строку дерева,
    # эмулируя hard-delete-erasure scenario. Эта проверка касается только
    # FK CASCADE на DB-уровне.
    await db_session.execute(text("DELETE FROM trees WHERE id = :tid"), {"tid": tree.id})
    await db_session.flush()

    after = (
        await db_session.execute(select(AudioSession).where(AudioSession.id == audio_id))
    ).scalar_one_or_none()
    assert after is None, "audio_sessions должен быть удалён каскадом FK"


@pytest.mark.db
@pytest.mark.integration
async def test_audio_session_tree_consent_columns_present(
    db_session: AsyncSession,
) -> None:
    """``trees`` имеет два consent-поля и принимает NULL по умолчанию."""
    _, tree = await _seed_user_and_tree(db_session)
    fetched = (await db_session.execute(select(Tree).where(Tree.id == tree.id))).scalar_one()
    assert fetched.audio_consent_egress_at is None
    assert fetched.audio_consent_egress_provider is None

    fetched.audio_consent_egress_at = dt.datetime.now(dt.UTC)
    fetched.audio_consent_egress_provider = "openai"
    await db_session.flush()

    refreshed = (await db_session.execute(select(Tree).where(Tree.id == tree.id))).scalar_one()
    assert refreshed.audio_consent_egress_at is not None
    assert refreshed.audio_consent_egress_provider == "openai"
