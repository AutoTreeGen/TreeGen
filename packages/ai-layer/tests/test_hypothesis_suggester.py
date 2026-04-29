"""Тесты use-case ``HypothesisSuggester``."""

from __future__ import annotations

import json

import pytest
from _fakes import FakeMessage, FakeTextBlock, FakeUsage
from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.config import AILayerConfig
from ai_layer.use_cases.hypothesis_suggestion import (
    FabricatedEvidenceError,
    HypothesisSuggester,
    PersonFact,
)
from pydantic import ValidationError


def _facts() -> list[PersonFact]:
    return [
        PersonFact(id="p:1:birth", text="Person 1 born 1850 in Vilna"),
        PersonFact(id="p:2:birth", text="Person 2 born 1855 in Vilna"),
    ]


@pytest.mark.asyncio
async def test_suggest_happy_path(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    payload = {
        "rationale": "Both persons born in same town within 5 years — possible siblings.",
        "confidence": 0.55,
        "evidence_refs": ["p:1:birth", "p:2:birth"],
    }

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text=json.dumps(payload))],
            usage=FakeUsage(input_tokens=100, output_tokens=20),
        )

    fake = make_fake_anthropic(responder)
    suggester = HypothesisSuggester(AnthropicClient(enabled_config, client=fake))

    result = await suggester.suggest(_facts(), existing_hypotheses=[])

    assert result.parsed.confidence == pytest.approx(0.55)
    assert result.parsed.evidence_refs == ["p:1:birth", "p:2:birth"]
    # Промпт реально рендерился с фактами.
    sent_user = fake.messages.calls[0]["messages"][0]["content"]
    assert "p:1:birth" in sent_user
    assert "Vilna" in sent_user


@pytest.mark.asyncio
async def test_fabricated_evidence_rejected(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """LLM сослался на несуществующий ID → use-case бросает ошибку."""
    payload = {
        "rationale": "Made up — cites a fact that does not exist",
        "confidence": 0.9,
        "evidence_refs": ["p:99:birth"],  # отсутствует во входе
    }

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload))])

    suggester = HypothesisSuggester(
        AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    )
    with pytest.raises(FabricatedEvidenceError, match="p:99:birth"):
        await suggester.suggest(_facts())


@pytest.mark.asyncio
async def test_empty_evidence_refs_allowed_for_refusal(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """LLM имеет право отказаться (`evidence_refs=[]`) — не считается галлюцинацией."""
    payload = {
        "rationale": "Insufficient evidence",
        "confidence": 0.0,
        "evidence_refs": [],
    }

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload))])

    suggester = HypothesisSuggester(
        AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    )
    result = await suggester.suggest(_facts())
    assert result.parsed.confidence == pytest.approx(0.0)
    assert result.parsed.evidence_refs == []


@pytest.mark.asyncio
async def test_invalid_confidence_raises_validation_error(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """``confidence > 1.0`` → Pydantic ValidationError на стадии парсинга."""
    payload = {
        "rationale": "x",
        "confidence": 1.5,
        "evidence_refs": [],
    }

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload))])

    suggester = HypothesisSuggester(
        AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    )
    with pytest.raises(ValidationError):
        await suggester.suggest(_facts())


@pytest.mark.asyncio
async def test_existing_hypotheses_passed_into_prompt(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    payload = {"rationale": "ok", "confidence": 0.3, "evidence_refs": []}

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text=json.dumps(payload))])

    fake = make_fake_anthropic(responder)
    suggester = HypothesisSuggester(AnthropicClient(enabled_config, client=fake))
    await suggester.suggest(
        _facts(),
        existing_hypotheses=["p:1 is parent of p:2 (rejected, age gap too small)"],
    )
    sent_user = fake.messages.calls[0]["messages"][0]["content"]
    assert "rejected, age gap too small" in sent_user
