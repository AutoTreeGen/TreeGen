"""Phase 10.3 / ADR-0060 — AI normalization API tests.

Этот файл — НЕ-DB tests: endpoints `/places/normalize` / `/names/normalize`
не читают/не пишут БД (нормализация — read-only LLM-вызов + Redis-counter).
Используем in-memory fakeredis вместо testcontainer'а.

Покрывает:

* Happy path place/name → 200 + структурированный ответ.
* Kill switch: AI_LAYER_ENABLED=false → 503.
* Empty raw → 422.
* Rate limit: 11-й вызов за день → 429.
* Voyage candidates → ranked top-K в ответе.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import pytest_asyncio
from ai_layer.types import (
    CandidateMatch,
    NameNormalization,
    NormalizationResult,
    PlaceNormalization,
)
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def ai_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_LAYER_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    server = fakeredis.aioredis.FakeServer()
    return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def override_normalize_deps(app, fake_redis: fakeredis.aioredis.FakeRedis):
    """Override place/name normalizer + redis client deps.

    Yields a ``deps`` dict so tests can mutate the AsyncMock'и
    (``deps["place"].normalize.return_value = ...``).
    """
    from parser_service.api.normalize import (
        get_name_normalizer,
        get_place_normalizer,
        get_redis_client,
    )

    place_mock = AsyncMock()
    name_mock = AsyncMock()
    app.dependency_overrides[get_place_normalizer] = lambda: place_mock
    app.dependency_overrides[get_name_normalizer] = lambda: name_mock
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    yield {"place": place_mock, "name": name_mock, "redis": fake_redis}
    app.dependency_overrides.pop(get_place_normalizer, None)
    app.dependency_overrides.pop(get_name_normalizer, None)
    app.dependency_overrides.pop(get_redis_client, None)


@pytest_asyncio.fixture
async def normalize_client(app) -> AsyncIterator[AsyncClient]:
    """httpx-клиент против app — БЕЗ postgres-инициализации.

    normalize-эндпоинты не лазят в БД, поэтому postgres testcontainer
    нам не нужен (экономим setup-time на ~5 секунд per run).
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _ok_place_result() -> NormalizationResult:
    return NormalizationResult(
        kind="place",
        place=PlaceNormalization(
            canonical_name="Yuzerin",
            country_modern="Belarus",
            country_historical="Russian Empire",
            admin1="Gomel Region",
            admin2=None,
            settlement="village",
            latitude=None,
            longitude=None,
            confidence=0.62,
            ethnicity_hint="ashkenazi_jewish",
            alternative_forms=["Юзерин"],
            notes=None,
        ),
        candidates=[],
        input_tokens=600,
        output_tokens=200,
        cost_usd=0.0048,
        model="claude-sonnet-4-6",
        dry_run=False,
    )


def _ok_name_result() -> NormalizationResult:
    return NormalizationResult(
        kind="name",
        name=NameNormalization(
            given="Ivan",
            surname="Zhidnitsky",
            patronymic="Petrovich",
            given_alts=["Иван"],
            surname_alts=["Жидницкий"],
            script_detected="cyrillic",
            transliteration_scheme="bgn_pcgn",
            ethnicity_hint="slavic",
            tribe_marker="unknown",
            confidence=0.85,
        ),
        candidates=[],
        input_tokens=700,
        output_tokens=250,
        cost_usd=0.00585,
        model="claude-sonnet-4-6",
        dry_run=False,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_place_normalize_happy_path(
    normalize_client: AsyncClient,
    override_normalize_deps: dict[str, Any],
) -> None:
    override_normalize_deps["place"].normalize = AsyncMock(return_value=_ok_place_result())

    resp = await normalize_client.post(
        "/places/normalize",
        json={"raw": "Юзерин, Гомельская обл", "locale_hint": "ru"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["place"]["canonical_name"] == "Yuzerin"
    assert body["place"]["country_modern"] == "Belarus"
    assert body["input_tokens"] == 600
    assert body["output_tokens"] == 200
    assert body["model"] == "claude-sonnet-4-6"
    assert body["dry_run"] is False
    # 10 default - 1 used = 9.
    assert body["budget_remaining_runs"] == 9


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_name_normalize_happy_path(
    normalize_client: AsyncClient,
    override_normalize_deps: dict[str, Any],
) -> None:
    override_normalize_deps["name"].normalize = AsyncMock(return_value=_ok_name_result())

    resp = await normalize_client.post(
        "/names/normalize",
        json={"raw": "Иван Петрович Жидницкий", "script_hint": "cyrillic"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"]["given"] == "Ivan"
    assert body["name"]["patronymic"] == "Petrovich"
    assert body["name"]["script_detected"] == "cyrillic"


@pytest.mark.asyncio
async def test_kill_switch_returns_503(
    normalize_client: AsyncClient,
    override_normalize_deps: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AI_LAYER_ENABLED`` unset → ensure_ai_layer_enabled бросает → 503."""
    monkeypatch.delenv("AI_LAYER_ENABLED", raising=False)

    resp = await normalize_client.post(
        "/places/normalize",
        json={"raw": "Юзерин"},
    )
    assert resp.status_code == 503, resp.text
    assert "disabled" in resp.text.lower()
    # Normalizer не должен был вызваться.
    override_normalize_deps["place"].normalize.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_normalize_deps")
async def test_empty_raw_returns_422(
    normalize_client: AsyncClient,
) -> None:
    # Pydantic-уровень: min_length=1 → 422 (FastAPI поднимает без вызова handler'а).
    resp = await normalize_client.post(
        "/places/normalize",
        json={"raw": ""},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_whitespace_only_raw_returns_422_via_use_case(
    normalize_client: AsyncClient,
    override_normalize_deps: dict[str, Any],
) -> None:
    """Pydantic пропустит '   ' (min_length=1 это 3 символа), а use-case бросит EmptyInputError → 422."""
    from ai_layer.use_cases.normalize import EmptyInputError

    override_normalize_deps["place"].normalize = AsyncMock(
        side_effect=EmptyInputError("Raw input is empty after strip")
    )
    resp = await normalize_client.post(
        "/places/normalize",
        json={"raw": "   "},
    )
    assert resp.status_code == 422
    assert "empty" in resp.text.lower()


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_rate_limit_429(
    normalize_client: AsyncClient,
    override_normalize_deps: dict[str, Any],
) -> None:
    """11-й вызов за день → 429 BudgetExceededError."""
    override_normalize_deps["place"].normalize = AsyncMock(return_value=_ok_place_result())

    # 10 runs allowed (default).
    for i in range(10):
        resp = await normalize_client.post(
            "/places/normalize",
            json={"raw": f"Place {i}"},
        )
        assert resp.status_code == 200, f"call {i + 1}: {resp.text}"

    # 11-й — 429.
    blocked = await normalize_client.post(
        "/places/normalize",
        json={"raw": "Place 11"},
    )
    assert blocked.status_code == 429, blocked.text
    detail = blocked.json()["detail"]
    assert detail["limit_kind"] == "runs_per_day"
    assert detail["limit_value"] == 10


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_candidates_match_passes_through(
    normalize_client: AsyncClient,
    override_normalize_deps: dict[str, Any],
) -> None:
    """Voyage-ranked candidates пробрасываются в response."""
    place_with_candidates = _ok_place_result()
    place_with_candidates = place_with_candidates.model_copy(
        update={
            "candidates": [
                CandidateMatch(
                    candidate_id="p1",
                    candidate_text="Yuzerin (Belarus)",
                    score=0.93,
                    rank=1,
                ),
                CandidateMatch(
                    candidate_id="p2",
                    candidate_text="Yuzeryn",
                    score=0.81,
                    rank=2,
                ),
            ]
        }
    )
    override_normalize_deps["place"].normalize = AsyncMock(return_value=place_with_candidates)

    resp = await normalize_client.post(
        "/places/normalize",
        json={
            "raw": "Юзерин",
            "candidates": [
                {"id": "p1", "text": "Yuzerin (Belarus)"},
                {"id": "p2", "text": "Yuzeryn"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["candidate_id"] == "p1"
    assert body["candidates"][0]["rank"] == 1
    assert body["candidates"][0]["score"] > body["candidates"][1]["score"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_normalize_deps")
async def test_too_long_raw_validated_by_pydantic(
    normalize_client: AsyncClient,
) -> None:
    resp = await normalize_client.post(
        "/places/normalize",
        json={"raw": "x" * 2000},  # > schema max_length=1024
    )
    assert resp.status_code == 422
