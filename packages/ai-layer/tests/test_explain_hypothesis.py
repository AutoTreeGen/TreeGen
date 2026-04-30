"""Тесты use-case ``HypothesisExplainer`` (Phase 10.1).

Реальные вызовы Anthropic запрещены — используется FakeAnthropic stub.
Сценарии:

- dry-run mode: возвращается mock без сетевых вызовов;
- happy-path: prompt содержит ключевые правила, JSON парсится;
- malformed JSON: один retry, потом fail-soft summary;
- locale=ru: системный промпт переключает язык ответа;
- truncation: 100 evidence-items → prompt в пределах MAX_PROMPT_CHARS,
  оставляются top-N по confidence.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from _fakes import FakeMessage, FakeTextBlock, FakeUsage
from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.config import AILayerConfig
from ai_layer.types import (
    EvidenceItem,
    HypothesisExplanation,
    HypothesisInput,
    PersonSubject,
)
from ai_layer.use_cases.explain_hypothesis import (
    DRY_RUN_ENV_VAR,
    MAX_EVIDENCE_ITEMS,
    MAX_PROMPT_CHARS,
    HypothesisExplainer,
    _truncate_evidence,
)


def _subjects() -> tuple[PersonSubject, PersonSubject]:
    return (
        PersonSubject(id="p:1", summary="Iosif Kaminskii, b. 1872 Vilna"),
        PersonSubject(id="p:2", summary="Joseph Kaminsky, b. 1872 Vilna"),
    )


def _evidence(n: int = 3) -> list[EvidenceItem]:
    base = [
        EvidenceItem(
            rule_id="rule.name.fuzzy",
            confidence=0.92,
            direction="supports",
            details="Names align under Russian-to-English transliteration",
        ),
        EvidenceItem(
            rule_id="rule.birth_year.exact",
            confidence=1.0,
            direction="supports",
            details="Birth year 1872 matches exactly on both records",
        ),
        EvidenceItem(
            rule_id="rule.birthplace.exact",
            confidence=1.0,
            direction="supports",
            details="Birthplace 'Vilna' matches exactly on both records",
        ),
    ]
    return base[:n]


def _hypothesis() -> HypothesisInput:
    return HypothesisInput(
        subjects=_subjects(),
        evidence=_evidence(),
        composite_score=0.85,
    )


def _good_payload() -> dict[str, Any]:
    return {
        "summary": "Strong same-person match: birth year and place exact, names transliterated.",
        "key_evidence": [
            "Birth year exact match (1872)",
            "Birthplace exact match (Vilna)",
            "Names align under standard transliteration",
        ],
        "caveats": ["No DNA evidence supplied"],
        "confidence_label": "high",
    }


def _good_responder(*, usage: FakeUsage | None = None) -> Callable[..., FakeMessage]:
    payload = _good_payload()

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text=json.dumps(payload))],
            usage=usage or FakeUsage(input_tokens=2_500, output_tokens=300),
        )

    return responder


@pytest.mark.asyncio
async def test_dry_run_returns_mock_without_calling_anthropic(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """``AI_DRY_RUN=true`` → ответ не зависит от Anthropic."""
    fake = make_fake_anthropic(_good_responder())  # responder не должен вызваться
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={DRY_RUN_ENV_VAR: "true"},
    )

    result = await explainer.explain(_hypothesis(), locale="en")

    assert isinstance(result, HypothesisExplanation)
    assert result.dry_run is True
    assert result.tokens_used == 0
    assert result.cost_usd == 0.0
    assert result.model == "dry-run"
    assert result.locale == "en"
    assert "match" in result.summary.lower()
    assert fake.messages.calls == [], "Anthropic must not be called in dry-run"


@pytest.mark.asyncio
async def test_dry_run_localized_to_ru(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_good_responder())
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={DRY_RUN_ENV_VAR: "true"},
    )
    result = await explainer.explain(_hypothesis(), locale="ru")
    assert result.locale == "ru"
    assert any(ord(ch) > 127 for ch in result.summary), (
        "Russian dry-run summary must contain non-ASCII characters"
    )


@pytest.mark.asyncio
async def test_happy_path_invokes_anthropic_with_correct_prompt(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_good_responder())
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={},  # AI_DRY_RUN is unset
    )

    result = await explainer.explain(_hypothesis(), locale="en")

    assert isinstance(result, HypothesisExplanation)
    assert result.dry_run is False
    assert result.locale == "en"
    assert result.confidence_label == "high"
    assert result.tokens_used == 2_800  # 2_500 + 300
    assert result.cost_usd > 0

    assert len(fake.messages.calls) == 1
    call = fake.messages.calls[0]
    system_prompt = call["system"]
    user_prompt = call["messages"][0]["content"]

    # System prompt должен содержать ключевые правила.
    assert "senior genealogist" in system_prompt.lower()
    assert "invent facts" in system_prompt.lower()
    assert "caveat" in system_prompt.lower()
    assert "confidence_label" in system_prompt.lower()

    # User prompt должен содержать subjects и evidence.
    assert "p:1" in user_prompt
    assert "p:2" in user_prompt
    assert "Vilna" in user_prompt
    assert "rule.birth_year.exact" in user_prompt
    assert "0.85" in user_prompt  # composite_score


@pytest.mark.asyncio
async def test_locale_ru_switches_system_prompt_language_directive(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """В русском режиме system prompt прямо требует ответ на русском."""
    fake = make_fake_anthropic(_good_responder())
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    await explainer.explain(_hypothesis(), locale="ru")
    system_prompt = fake.messages.calls[0]["system"]
    assert "respond in **russian**" in system_prompt.lower(), (
        "system prompt must instruct LLM to respond in Russian"
    )


@pytest.mark.asyncio
async def test_malformed_json_retries_once_then_fails_soft(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Malformed JSON оба раза → fail-soft `HypothesisExplanation`, без exception."""
    call_count = {"n": 0}

    def responder(**_: object) -> FakeMessage:
        call_count["n"] += 1
        return FakeMessage(
            content=[FakeTextBlock(text="not valid json {")],
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        )

    fake = make_fake_anthropic(responder)
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await explainer.explain(_hypothesis(), locale="en")

    assert call_count["n"] == 2, "must retry exactly once before failing soft"
    assert result.confidence_label == "low"
    assert result.model == "error"
    assert result.tokens_used == 0
    assert "invalid" in result.summary.lower() or "could not" in result.summary.lower()
    assert result.caveats, "fail-soft response must include reason in caveats"


@pytest.mark.asyncio
async def test_malformed_json_succeeds_on_retry(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Первый ответ malformed, второй ok → пользователь получает корректное объяснение."""
    payload = _good_payload()
    sequence = iter(
        [
            FakeMessage(
                content=[FakeTextBlock(text="garbage")],
                usage=FakeUsage(input_tokens=10, output_tokens=5),
            ),
            FakeMessage(
                content=[FakeTextBlock(text=json.dumps(payload))],
                usage=FakeUsage(input_tokens=2_500, output_tokens=300),
            ),
        ]
    )

    def responder(**_: object) -> FakeMessage:
        return next(sequence)

    fake = make_fake_anthropic(responder)
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await explainer.explain(_hypothesis(), locale="en")
    assert result.confidence_label == "high"
    assert result.model != "error"
    assert result.tokens_used == 2_800


@pytest.mark.asyncio
async def test_stress_truncates_to_max_evidence_items(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """100 evidence-items → prompt не превышает 100k символов."""
    big_evidence = [
        EvidenceItem(
            rule_id=f"rule.synthetic.{i}",
            # confidence убывает, чтобы truncate сохранил первые items
            confidence=max(0.0, 1.0 - i / 100),
            direction="neutral",
            details=f"Synthetic evidence item #{i} for stress testing",
        )
        for i in range(100)
    ]
    inp = HypothesisInput(
        subjects=_subjects(),
        evidence=big_evidence,
    )

    fake = make_fake_anthropic(_good_responder())
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    await explainer.explain(inp, locale="en")

    user_prompt = fake.messages.calls[0]["messages"][0]["content"]
    system_prompt = fake.messages.calls[0]["system"]
    assert len(system_prompt) + len(user_prompt) <= MAX_PROMPT_CHARS

    # Top-N сохранены: первый item (highest confidence) есть, последний — нет.
    assert "rule.synthetic.0" in user_prompt
    assert "rule.synthetic.99" not in user_prompt


def test_truncate_evidence_preserves_order_for_equal_confidence() -> None:
    """Stable-sort: items с одинаковой confidence сохраняют исходный порядок."""
    items = [
        EvidenceItem(rule_id=f"r.{i}", confidence=0.5, direction="neutral", details=f"d{i}")
        for i in range(MAX_EVIDENCE_ITEMS + 5)
    ]
    kept = _truncate_evidence(items)
    assert len(kept) == MAX_EVIDENCE_ITEMS
    assert [item.rule_id for item in kept] == [f"r.{i}" for i in range(MAX_EVIDENCE_ITEMS)]


def test_truncate_evidence_no_op_when_below_limit() -> None:
    items = _evidence(3)
    kept = _truncate_evidence(items)
    assert kept == items


@pytest.mark.asyncio
async def test_disabled_config_raises_via_anthropic_client(
    disabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """``enabled=false`` без dry-run → AnthropicClient бросает AILayerDisabledError."""
    from ai_layer.config import AILayerDisabledError

    fake = make_fake_anthropic(_good_responder())
    explainer = HypothesisExplainer(
        AnthropicClient(disabled_config, client=fake),
        env={},
    )
    with pytest.raises(AILayerDisabledError):
        await explainer.explain(_hypothesis(), locale="en")


@pytest.mark.asyncio
async def test_empty_evidence_still_renders(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Пустой evidence — допустимо; LLM получит '0 items' user-prompt."""
    inp = HypothesisInput(subjects=_subjects(), evidence=[])
    fake = make_fake_anthropic(_good_responder())
    explainer = HypothesisExplainer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    await explainer.explain(inp, locale="en")
    user_prompt = fake.messages.calls[0]["messages"][0]["content"]
    assert "Evidence (0 item" in user_prompt
