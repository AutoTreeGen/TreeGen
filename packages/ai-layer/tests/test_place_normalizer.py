"""Tests for ``PlaceNormalizer`` (Phase 10.3 / ADR-0060).

Все вызовы Anthropic — через ``FakeAnthropic`` stub. Voyage не вызываем
(candidate-match покрывается в ``test_normalize_match.py``).

Сценарии:

* dry-run mode — mock без сетевых вызовов.
* happy-path: Cyrillic input → Latin canonical_name + ethnicity_hint.
* malformed JSON: один retry, потом fail-soft.
* empty / too-large input → ``EmptyInputError`` / ``RawInputTooLargeError``.
* locale_hint и context рендерятся в user-prompt.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from _fakes import FakeMessage, FakeTextBlock, FakeUsage
from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.config import AILayerConfig
from ai_layer.types import NormalizationResult
from ai_layer.use_cases.normalize import (
    DRY_RUN_ENV_VAR,
    EmptyInputError,
    PlaceNormalizer,
    RawInputTooLargeError,
)


def _good_place_payload() -> dict[str, Any]:
    return {
        "canonical_name": "Yuzerin",
        "country_modern": "Belarus",
        "country_historical": "Russian Empire",
        "admin1": "Gomel Region",
        "admin2": None,
        "settlement": "village",
        "latitude": None,
        "longitude": None,
        "confidence": 0.62,
        "ethnicity_hint": "ashkenazi_jewish",
        "alternative_forms": ["Юзерин", "Yuzeryn"],
        "notes": "Pale of Settlement; small Jewish community.",
    }


def _ok_responder(payload: dict[str, Any], *, in_tok: int = 600, out_tok: int = 200):
    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text=json.dumps(payload))],
            usage=FakeUsage(input_tokens=in_tok, output_tokens=out_tok),
        )

    return responder


@pytest.mark.asyncio
async def test_dry_run_returns_mock_without_calling_anthropic(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """``AI_DRY_RUN=true`` → ответ не зависит от Anthropic."""
    fake = make_fake_anthropic(_ok_responder(_good_place_payload()))
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={DRY_RUN_ENV_VAR: "true"},
    )

    result = await normalizer.normalize("Юзерин, Гомельская обл")

    assert isinstance(result, NormalizationResult)
    assert result.kind == "place"
    assert result.dry_run is True
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.cost_usd == 0.0
    assert result.model == "dry-run"
    assert fake.messages.calls == [], "Anthropic must not be called in dry-run"


@pytest.mark.asyncio
async def test_happy_path_renders_prompt_and_parses_response(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_ok_responder(_good_place_payload()))
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )

    result = await normalizer.normalize(
        "Юзерин, Гомельская обл",
        locale_hint="ru",
        context="Family lived there until 1905 emigration.",
    )

    assert result.kind == "place"
    assert result.place is not None
    assert result.place.canonical_name == "Yuzerin"
    assert result.place.country_modern == "Belarus"
    assert result.place.ethnicity_hint == "ashkenazi_jewish"
    assert result.input_tokens == 600
    assert result.output_tokens == 200
    assert result.cost_usd > 0
    assert result.dry_run is False

    user_prompt = fake.messages.calls[0]["messages"][0]["content"]
    system_prompt = fake.messages.calls[0]["system"]
    assert "Юзерин" in user_prompt
    assert "ru" in user_prompt  # locale_hint rendered
    assert "1905 emigration" in user_prompt  # context rendered
    # Hard rules present in system prompt:
    assert "invent" in system_prompt.lower()
    assert "pale of settlement" in system_prompt.lower()
    assert "bgn/pcgn" in system_prompt.lower()


@pytest.mark.asyncio
async def test_empty_input_raises(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_ok_responder(_good_place_payload()))
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    with pytest.raises(EmptyInputError):
        await normalizer.normalize("   ")
    assert fake.messages.calls == []


@pytest.mark.asyncio
async def test_too_large_input_raises(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_ok_responder(_good_place_payload()))
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    with pytest.raises(RawInputTooLargeError):
        await normalizer.normalize("x" * 2000)
    assert fake.messages.calls == []


@pytest.mark.asyncio
async def test_malformed_json_retries_once_then_fails_soft(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Malformed JSON оба раза → fail-soft с low-confidence place, без exception."""
    call_count = {"n": 0}

    def responder(**_: object) -> FakeMessage:
        call_count["n"] += 1
        return FakeMessage(
            content=[FakeTextBlock(text="not valid json {")],
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        )

    fake = make_fake_anthropic(responder)
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await normalizer.normalize("Brody")

    assert call_count["n"] == 2, "must retry exactly once"
    assert result.place is not None
    assert result.place.confidence == 0.0
    assert result.place.canonical_name == "(unrecognized)"
    assert result.model == "error"
    assert "AI normalization failed" in (result.place.notes or "")


@pytest.mark.asyncio
async def test_disabled_config_raises(
    disabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """``enabled=false`` без dry-run → AILayerDisabledError, не fail-soft."""
    from ai_layer.config import AILayerDisabledError

    fake = make_fake_anthropic(_ok_responder(_good_place_payload()))
    normalizer = PlaceNormalizer(
        AnthropicClient(disabled_config, client=fake),
        env={},
    )
    with pytest.raises(AILayerDisabledError):
        await normalizer.normalize("Brody")


@pytest.mark.asyncio
async def test_polish_galician_input(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Synthetic Polish/Galician example."""
    payload = {
        "canonical_name": "Brody",
        "country_modern": "Ukraine",
        "country_historical": "Austrian Empire (Galicia)",
        "admin1": "Lviv Oblast",
        "admin2": "Brody Raion",
        "settlement": "town",
        "latitude": 50.0833,
        "longitude": 25.15,
        "confidence": 0.92,
        "ethnicity_hint": "ashkenazi_jewish",
        "alternative_forms": ["Бро́ди", "ברודי"],
        "notes": "Major Jewish trade hub in 19th century.",
    }
    fake = make_fake_anthropic(_ok_responder(payload))
    normalizer = PlaceNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await normalizer.normalize("Brody, Galicia, Austria")
    assert result.place is not None
    assert result.place.country_modern == "Ukraine"
    assert result.place.country_historical is not None
    assert "Austrian" in result.place.country_historical
    assert result.place.latitude is not None
    assert result.place.longitude is not None
