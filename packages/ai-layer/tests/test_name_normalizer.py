"""Tests for ``NameNormalizer`` (Phase 10.3 / ADR-0060).

Synthetic examples cover:

* Russian Cyrillic with patronymic.
* Hebrew with HaKohen marker.
* Polish maiden-name (née) convention.
* Yiddish + diminutive in parentheses.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from _fakes import FakeMessage, FakeTextBlock, FakeUsage
from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.config import AILayerConfig
from ai_layer.use_cases.normalize import (
    DRY_RUN_ENV_VAR,
    EmptyInputError,
    NameNormalizer,
    RawInputTooLargeError,
)


def _name_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "given": "Ivan",
        "surname": "Zhidnitsky",
        "patronymic": "Petrovich",
        "maiden_surname": None,
        "prefix": None,
        "suffix": None,
        "nickname": None,
        "given_alts": ["Иван", "Iwan"],
        "surname_alts": ["Жидницкий", "Żydnicki"],
        "script_detected": "cyrillic",
        "transliteration_scheme": "bgn_pcgn",
        "ethnicity_hint": "slavic",
        "tribe_marker": "unknown",
        "confidence": 0.86,
        "notes": None,
    }
    base.update(overrides)
    return base


def _ok_responder(payload: dict[str, Any], *, in_tok: int = 700, out_tok: int = 250):
    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text=json.dumps(payload))],
            usage=FakeUsage(input_tokens=in_tok, output_tokens=out_tok),
        )

    return responder


@pytest.mark.asyncio
async def test_dry_run_returns_mock(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_ok_responder(_name_payload()))
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={DRY_RUN_ENV_VAR: "1"},
    )
    result = await normalizer.normalize("anything")
    assert result.kind == "name"
    assert result.dry_run is True
    assert fake.messages.calls == []


@pytest.mark.asyncio
async def test_russian_cyrillic_with_patronymic(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_ok_responder(_name_payload()))
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await normalizer.normalize(
        "Иван Петрович Жидницкий",
        script_hint="cyrillic",
        locale_hint="ru",
    )
    assert result.name is not None
    assert result.name.given == "Ivan"
    assert result.name.patronymic == "Petrovich"
    assert result.name.script_detected == "cyrillic"
    assert result.name.transliteration_scheme == "bgn_pcgn"
    assert result.name.tribe_marker == "unknown"

    user_prompt = fake.messages.calls[0]["messages"][0]["content"]
    system_prompt = fake.messages.calls[0]["system"]
    assert "Иван Петрович Жидницкий" in user_prompt
    assert "cyrillic" in user_prompt  # script_hint
    # Hard rule about explicit kohen/levi marking:
    assert "kohen" in system_prompt.lower()
    assert "infer from a surname" in system_prompt.lower()


@pytest.mark.asyncio
async def test_hebrew_with_kohen_marker(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    payload = _name_payload(
        given="Meir",
        surname=None,
        patronymic="ben Avraham",
        suffix="HaKohen",
        given_alts=["Meyer", "Майер"],
        surname_alts=[],
        script_detected="hebrew",
        transliteration_scheme="ala_lc",
        ethnicity_hint="ashkenazi_jewish",
        tribe_marker="kohen",
        confidence=0.94,
    )
    fake = make_fake_anthropic(_ok_responder(payload))
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await normalizer.normalize(
        "מאיר בן אברהם הכהן",
        script_hint="hebrew",
    )
    assert result.name is not None
    assert result.name.given == "Meir"
    assert result.name.tribe_marker == "kohen"
    assert result.name.suffix == "HaKohen"
    assert result.name.ethnicity_hint == "ashkenazi_jewish"


@pytest.mark.asyncio
async def test_polish_maiden_surname(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    payload = _name_payload(
        given="Anna",
        surname="Goldberg",
        patronymic=None,
        maiden_surname="Kaminska",
        given_alts=["Hannah", "Anya"],
        surname_alts=["Goldberg", "Goldberger"],
        script_detected="latin",
        transliteration_scheme="none",
        ethnicity_hint="ashkenazi_jewish",
        tribe_marker="unknown",
        confidence=0.9,
    )
    fake = make_fake_anthropic(_ok_responder(payload))
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await normalizer.normalize("Anna Goldberg née Kaminska")
    assert result.name is not None
    assert result.name.maiden_surname == "Kaminska"
    assert result.name.surname == "Goldberg"


@pytest.mark.asyncio
async def test_yiddish_with_diminutive_nickname(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    payload = _name_payload(
        given="Iosif",
        surname="Kaminskii",
        patronymic=None,
        nickname="Yossi",
        given_alts=["Joseph", "Yosef", "Иосиф"],
        surname_alts=["Kaminsky", "Каминский", "Kamiński"],
        script_detected="latin",
        transliteration_scheme="none",
        ethnicity_hint="ashkenazi_jewish",
    )
    fake = make_fake_anthropic(_ok_responder(payload))
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await normalizer.normalize("Iosif Kaminskii (Yossi)")
    assert result.name is not None
    assert result.name.nickname == "Yossi"
    assert "Yosef" in result.name.given_alts


@pytest.mark.asyncio
async def test_empty_input_raises(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_ok_responder(_name_payload()))
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    with pytest.raises(EmptyInputError):
        await normalizer.normalize("")


@pytest.mark.asyncio
async def test_too_large_input_raises(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    fake = make_fake_anthropic(_ok_responder(_name_payload()))
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    with pytest.raises(RawInputTooLargeError):
        await normalizer.normalize("x" * 2048)


@pytest.mark.asyncio
async def test_fail_soft_on_malformed_json(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text="garbage {")],
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        )

    fake = make_fake_anthropic(responder)
    normalizer = NameNormalizer(
        AnthropicClient(enabled_config, client=fake),
        env={},
    )
    result = await normalizer.normalize("Test")
    assert result.name is not None
    assert result.name.confidence == 0.0
    assert result.name.given is None
    assert result.model == "error"
