"""Tests for Voyage candidate-match in normalize use cases (Phase 10.3 / ADR-0060).

Покрывает интеграцию между LLM-нормализацией и Voyage-ranking:

* Один happy-path: 3 candidate'а → top-K с убывающими scores.
* Empty candidates → Voyage не вызывается, ``candidates=[]`` в результате.
* Voyage failure → use-case продолжает (logs warning), candidates пустой.
* Truncation: > MAX_CANDIDATES → урезание; primary still works.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from _fakes import FakeMessage, FakeTextBlock, FakeUsage, FakeVoyageResult
from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.clients.voyage_client import VoyageEmbeddingClient
from ai_layer.config import AILayerConfig
from ai_layer.use_cases.normalize import (
    MAX_CANDIDATES,
    CandidateRecord,
    PlaceNormalizer,
    _cosine_similarity,
)


def _ok_place_payload() -> dict[str, Any]:
    return {
        "canonical_name": "Brody",
        "country_modern": "Ukraine",
        "country_historical": "Austrian Empire",
        "admin1": "Lviv Oblast",
        "admin2": None,
        "settlement": "town",
        "latitude": 50.0833,
        "longitude": 25.15,
        "confidence": 0.9,
        "ethnicity_hint": "ashkenazi_jewish",
        "alternative_forms": ["Бро́ди"],
        "notes": None,
    }


def _llm_responder(payload: dict[str, Any]):
    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text=json.dumps(payload))],
            usage=FakeUsage(input_tokens=600, output_tokens=200),
        )

    return responder


def test_cosine_similarity_basics() -> None:
    """Smoke unit-test для pure-функции."""
    a = [1.0, 0.0, 0.0]
    assert _cosine_similarity(a, a) == pytest.approx(1.0)
    assert _cosine_similarity(a, [0.0, 1.0, 0.0]) == pytest.approx(0.0)
    # Orthogonal-anti — clip'аем в 0.
    assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == 0.0
    assert _cosine_similarity([], []) == 0.0
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


@pytest.mark.asyncio
async def test_candidate_match_ranks_by_similarity(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
    make_fake_voyage,
) -> None:
    """Voyage возвращает 4 vectors → ranked top-3 в результате."""
    fake_anthropic = make_fake_anthropic(_llm_responder(_ok_place_payload()))

    # Vectors: query "Brody" — [1, 0, 0]; cand1 "Brody, UA" — близкий [0.9, 0.1, 0];
    # cand2 "Brody, RO" — средний [0.7, 0.3, 0]; cand3 "Bratislava" — далёкий [0, 0, 1].
    def voyage_responder(*, texts: list[str], **_: object) -> FakeVoyageResult:
        # Verify dedup behaviour from VoyageEmbeddingClient already worked:
        # каждая уникальная строка приходит сюда один раз.
        mapping = {
            "Brody": [1.0, 0.0, 0.0],
            "Brody, UA": [0.9, 0.1, 0.0],
            "Brody, RO": [0.7, 0.3, 0.0],
            "Bratislava": [0.0, 0.0, 1.0],
        }
        vectors = [mapping[t] for t in texts]
        return FakeVoyageResult(embeddings=vectors)

    fake_voyage = make_fake_voyage(voyage_responder)

    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake_anthropic),
        voyage=VoyageEmbeddingClient(enabled_config, client=fake_voyage),
        env={},
    )

    candidates = [
        CandidateRecord(id="p1", text="Brody, UA"),
        CandidateRecord(id="p2", text="Brody, RO"),
        CandidateRecord(id="p3", text="Bratislava"),
    ]
    result = await normalizer.normalize(
        "Brody, Galicia, Austria",
        candidates=candidates,
        top_k=3,
    )

    assert len(result.candidates) == 3
    # Top — самый близкий.
    assert result.candidates[0].candidate_id == "p1"
    assert result.candidates[0].rank == 1
    assert result.candidates[0].score > result.candidates[1].score
    # Самый дальний — последний.
    assert result.candidates[-1].candidate_id == "p3"
    # Voyage был вызван ровно один раз.
    assert len(fake_voyage.calls) == 1


@pytest.mark.asyncio
async def test_no_candidates_skips_voyage(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
    make_fake_voyage,
) -> None:
    fake_anthropic = make_fake_anthropic(_llm_responder(_ok_place_payload()))
    fake_voyage = make_fake_voyage(lambda **_: FakeVoyageResult(embeddings=[[1.0, 0.0]]))
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake_anthropic),
        voyage=VoyageEmbeddingClient(enabled_config, client=fake_voyage),
        env={},
    )
    result = await normalizer.normalize("Brody")
    assert result.candidates == []
    assert fake_voyage.calls == [], "Voyage must not be called without candidates"


@pytest.mark.asyncio
async def test_voyage_failure_does_not_break_use_case(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
    make_fake_voyage,
) -> None:
    """Voyage-сбой → пустой candidates, но place-нормализация возвращается."""
    fake_anthropic = make_fake_anthropic(_llm_responder(_ok_place_payload()))

    def broken_voyage(**_: object) -> FakeVoyageResult:
        msg = "Voyage simulated outage"
        raise RuntimeError(msg)

    fake_voyage = make_fake_voyage(broken_voyage)
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake_anthropic),
        voyage=VoyageEmbeddingClient(enabled_config, client=fake_voyage),
        env={},
    )
    result = await normalizer.normalize(
        "Brody",
        candidates=[CandidateRecord(id="p1", text="Brody, UA")],
    )
    assert result.place is not None
    assert result.candidates == []


@pytest.mark.asyncio
async def test_max_candidates_truncates(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
    make_fake_voyage,
) -> None:
    """> MAX_CANDIDATES → use-case урезает до лимита."""
    fake_anthropic = make_fake_anthropic(_llm_responder(_ok_place_payload()))

    def voyage_responder(*, texts: list[str], **_: object) -> FakeVoyageResult:
        # 1 query + MAX_CANDIDATES (truncated) candidates.
        assert len(texts) == 1 + MAX_CANDIDATES
        return FakeVoyageResult(embeddings=[[1.0, 0.0] for _ in texts])

    fake_voyage = make_fake_voyage(voyage_responder)
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake_anthropic),
        voyage=VoyageEmbeddingClient(enabled_config, client=fake_voyage),
        env={},
    )
    too_many = [CandidateRecord(id=f"p{i}", text=f"Place {i}") for i in range(MAX_CANDIDATES + 25)]
    result = await normalizer.normalize("Brody", candidates=too_many, top_k=10)
    assert len(result.candidates) == 10
    # Все возвращённые candidate-id'ы должны быть из первых MAX_CANDIDATES.
    used_ids = {c.candidate_id for c in result.candidates}
    expected_pool = {f"p{i}" for i in range(MAX_CANDIDATES)}
    assert used_ids.issubset(expected_pool)
