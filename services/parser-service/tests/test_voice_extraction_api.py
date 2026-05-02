"""Phase 10.9b — integration tests для voice-extraction API (ADR-0075).

**Critical CI-blocking test:** ``test_post_without_consent_returns_403`` —
без consent'а POST НИКОГДА не должен триггерить extraction. Это тот же
privacy-gate, что в 10.9a (Anthropic — second egress channel).

Маркеры: ``db`` + ``integration`` (testcontainers Postgres).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import (
    AudioSession,
    Tree,
    TreeMembership,
    User,
    VoiceExtractedProposal,
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


@pytest.fixture
def nlu_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включить ``AI_LAYER_ENABLED=true`` + key чтобы POST не падал в 503.

    503-путь покрывается ``test_post_nlu_unavailable_returns_503``.
    """
    monkeypatch.setenv("AI_LAYER_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"voice-ext-{uuid.uuid4().hex[:8]}@example.com"
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
            name=f"Voice Ext Test {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
            # Дефолтно даём consent: нашему extraction-API он нужен
            # как «текущее tree-level состояние»; тесты, проверяющие
            # 403 без consent'а, NULL'ят его явно после создания.
            audio_consent_egress_at=dt.datetime.now(dt.UTC),
            audio_consent_egress_provider="openai",
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


async def _make_audio_session(
    factory: Any,
    *,
    tree: Tree,
    owner: User,
    transcript_text: str | None = "Анна Петровна родилась в Москве в 1925 году.",
    status: str = "ready",
    consent_granted: bool = True,
) -> AudioSession:
    """Создать AudioSession с заданным lifecycle-состоянием."""
    async with factory() as session:
        consent_at = dt.datetime.now(dt.UTC) if consent_granted else None
        session_row = AudioSession(
            tree_id=tree.id,
            owner_user_id=owner.id,
            storage_uri="memory://test/audio.webm",
            mime_type="audio/webm",
            size_bytes=1024,
            status=status,
            language="ru" if transcript_text else None,
            transcript_text=transcript_text,
            transcript_provider="openai-whisper-1" if transcript_text else None,
            transcript_model_version="whisper-1" if transcript_text else None,
            transcript_cost_usd=Decimal("0.001") if transcript_text else None,
            consent_egress_at=consent_at,  # type: ignore[arg-type]
            consent_egress_provider="openai" if consent_granted else None,  # type: ignore[arg-type]
            provenance={"upload_request_user_id": str(owner.id)},
        )
        session.add(session_row)
        await session.commit()
        await session.refresh(session_row)
        return session_row


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# CRITICAL — privacy gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("nlu_configured")
async def test_post_without_tree_consent_returns_403(app_client, session_factory: Any) -> None:
    """**CRITICAL CI-блок.** POST когда tree consent revoked → 403 ``consent_required``.

    Сессия имеет immutable snapshot consent_egress_at (DB NOT NULL), но
    tree-level consent может быть отозван между записью и extraction'ом.
    Anthropic — тот же egress-channel что Whisper; revocation честно
    блокирует extraction даже на сессии со snapshot.
    """
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    # Сессия с snapshot consent (legal state — DB NOT NULL).
    sess = await _make_audio_session(session_factory, tree=tree, owner=owner)
    # Симулируем revoke на tree уровне: NULL'им consent fields.
    async with session_factory() as ds:
        row = await ds.get(Tree, tree.id)
        row.audio_consent_egress_at = None
        row.audio_consent_egress_provider = None
        await ds.commit()

    r = await app_client.post(
        f"/audio-sessions/{sess.id}/extract",
        json={"force": False},
        headers=_hdr(owner),
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "consent_required"
    assert detail["tree_id"] == str(tree.id)


@pytest.mark.asyncio
@pytest.mark.usefixtures("nlu_configured")
async def test_post_transcript_not_ready_returns_409(app_client, session_factory: Any) -> None:
    """Сессия со status='uploaded' (транскрипт ещё не готов) → 409."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    sess = await _make_audio_session(
        session_factory,
        tree=tree,
        owner=owner,
        transcript_text=None,
        status="uploaded",
    )

    r = await app_client.post(
        f"/audio-sessions/{sess.id}/extract",
        json={"force": False},
        headers=_hdr(owner),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "transcript_not_ready"


@pytest.mark.asyncio
async def test_post_nlu_unavailable_returns_503(
    app_client,
    session_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет ``ANTHROPIC_API_KEY`` (и AI_LAYER_ENABLED=false) → 503."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AI_LAYER_ENABLED", "false")

    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    sess = await _make_audio_session(session_factory, tree=tree, owner=owner)

    r = await app_client.post(
        f"/audio-sessions/{sess.id}/extract",
        json={"force": False},
        headers=_hdr(owner),
    )
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "nlu_unavailable"


@pytest.mark.asyncio
@pytest.mark.usefixtures("nlu_configured")
async def test_post_happy_path_enqueues_job(app_client, session_factory: Any) -> None:
    """Happy path: 202 + extraction_job_id + arq-job enqueue'нут."""
    from parser_service.queue import get_arq_pool

    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    sess = await _make_audio_session(session_factory, tree=tree, owner=owner)

    fake_pool = app_client._transport.app.dependency_overrides[get_arq_pool]()

    r = await app_client.post(
        f"/audio-sessions/{sess.id}/extract",
        json={"force": False},
        headers=_hdr(owner),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["audio_session_id"] == str(sess.id)
    assert body["status"] == "queued"
    extraction_job_id = body["extraction_job_id"]

    # Job enqueue'нут с правильным name + args.
    extract_calls = [
        c
        for c in fake_pool.enqueue_job.call_args_list
        if c.args and c.args[0] == "voice_extract_job"
    ]
    assert len(extract_calls) == 1
    assert extract_calls[0].args[1] == str(sess.id)
    assert extract_calls[0].args[2] == extraction_job_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("nlu_configured")
async def test_get_extractions_groups_by_job_id(app_client, session_factory: Any) -> None:
    """GET /audio-sessions/{id}/extractions group-by extraction_job_id."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    sess = await _make_audio_session(session_factory, tree=tree, owner=owner)

    job_a = uuid.uuid4()
    job_b = uuid.uuid4()
    async with session_factory() as ds:
        ds.add(
            VoiceExtractedProposal(
                tree_id=tree.id,
                audio_session_id=sess.id,
                extraction_job_id=job_a,
                proposal_type="person",
                pass_number=1,
                status="pending",
                payload={"given_name": "Anna", "confidence": 0.9},
                confidence=Decimal("0.9"),
                evidence_snippets=["Анна"],
                raw_response={},
                model_version="claude-sonnet-4-6",
                prompt_version="voice_extract_pass1_v1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.001"),
                provenance={"job_status": "succeeded"},
            )
        )
        ds.add(
            VoiceExtractedProposal(
                tree_id=tree.id,
                audio_session_id=sess.id,
                extraction_job_id=job_b,
                proposal_type="place",
                pass_number=1,
                status="pending",
                payload={"name_raw": "Москва", "confidence": 0.95},
                confidence=Decimal("0.950"),
                evidence_snippets=["в Москве"],
                raw_response={},
                model_version="claude-sonnet-4-6",
                prompt_version="voice_extract_pass1_v1",
                input_tokens=120,
                output_tokens=40,
                cost_usd=Decimal("0.001"),
                provenance={"job_status": "partial_failed"},
            )
        )
        await ds.commit()

    r = await app_client.get(
        f"/audio-sessions/{sess.id}/extractions",
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audio_session_id"] == str(sess.id)
    assert body["total_jobs"] == 2
    job_ids = {j["extraction_job_id"] for j in body["jobs"]}
    assert job_ids == {str(job_a), str(job_b)}
    statuses = {j["status"] for j in body["jobs"]}
    assert statuses == {"succeeded", "partial_failed"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("nlu_configured")
async def test_get_extraction_job_returns_proposals(app_client, session_factory: Any) -> None:
    """GET /extractions/{job_id} → proposals одного job'а с status."""
    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    sess = await _make_audio_session(session_factory, tree=tree, owner=owner)
    job_id = uuid.uuid4()

    async with session_factory() as ds:
        for pass_n, ptype in [(1, "person"), (2, "relationship"), (3, "event")]:
            ds.add(
                VoiceExtractedProposal(
                    tree_id=tree.id,
                    audio_session_id=sess.id,
                    extraction_job_id=job_id,
                    proposal_type=ptype,
                    pass_number=pass_n,
                    status="pending",
                    payload={"confidence": 0.7},
                    confidence=Decimal("0.700"),
                    evidence_snippets=["snippet"],
                    raw_response={},
                    model_version="claude-sonnet-4-6",
                    prompt_version=f"voice_extract_pass{pass_n}_v1",
                    input_tokens=100,
                    output_tokens=30,
                    cost_usd=Decimal("0.001"),
                    provenance={"job_status": "succeeded"},
                )
            )
        await ds.commit()

    r = await app_client.get(
        f"/extractions/{job_id}",
        headers=_hdr(owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extraction_job_id"] == str(job_id)
    assert body["audio_session_id"] == str(sess.id)
    assert body["status"] == "succeeded"
    assert body["proposals_total"] == 3
    pass_numbers = sorted(p["pass_number"] for p in body["proposals"])
    assert pass_numbers == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.usefixtures("nlu_configured")
async def test_get_extraction_job_not_found(app_client, session_factory: Any) -> None:
    """Несуществующий extraction_job_id → 404."""
    owner = await _make_user(session_factory)
    # Тут даже tree/session не нужны — endpoint падает раньше.

    r = await app_client.get(
        f"/extractions/{uuid.uuid4()}",
        headers=_hdr(owner),
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("nlu_configured")
async def test_post_idempotent_returns_existing_job(app_client, session_factory: Any) -> None:
    """Повторный POST без force → возвращаем existing extraction_job_id."""
    from parser_service.queue import get_arq_pool

    owner = await _make_user(session_factory)
    tree = await _make_tree_with_owner(session_factory, owner=owner)
    sess = await _make_audio_session(session_factory, tree=tree, owner=owner)

    existing_job_id = uuid.uuid4()
    async with session_factory() as ds:
        ds.add(
            VoiceExtractedProposal(
                tree_id=tree.id,
                audio_session_id=sess.id,
                extraction_job_id=existing_job_id,
                proposal_type="person",
                pass_number=1,
                status="pending",
                payload={"confidence": 0.9},
                confidence=Decimal("0.9"),
                evidence_snippets=["snippet"],
                raw_response={},
                model_version="claude-sonnet-4-6",
                prompt_version="voice_extract_pass1_v1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.001"),
                provenance={"job_status": "succeeded"},
            )
        )
        await ds.commit()

    fake_pool = app_client._transport.app.dependency_overrides[get_arq_pool]()
    pre_count = len(fake_pool.enqueue_job.call_args_list)

    r = await app_client.post(
        f"/audio-sessions/{sess.id}/extract",
        json={"force": False},
        headers=_hdr(owner),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    # Идемпотентный возврат — НЕ создан новый job.
    assert body["extraction_job_id"] == str(existing_job_id)
    assert body["status"] == "succeeded"

    # Никаких новых enqueue.
    assert len(fake_pool.enqueue_job.call_args_list) == pre_count
