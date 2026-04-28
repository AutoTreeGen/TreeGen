"""Тесты ``normalize_place_name`` (Phase 10.0).

Реальный Anthropic API в CI не дёргается — все тесты используют
mock-клиент через conftest. Integration-тесты с настоящим API можно
добавить с маркером ``integration`` + ``slow`` (см. pyproject.toml
маркеры в корневом проекте).
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from llm_services import NormalizedPlace, normalize_place_name


@pytest.mark.asyncio
async def test_normalize_slonim_russian_empire(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """LLM канонизирует «Slonim, Russian Empire» в (Slonim, BY, ...)."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"name": "Slonim", "country_code": "BY", '
        '"historical_period": "Russian Empire (1795-1917)", '
        '"confidence": 0.95}'
    )

    result = await normalize_place_name(
        "Slonim, Russian Empire",
        context={"person_birth_year": 1880},
        client=mock_anthropic_client,
    )

    assert isinstance(result, NormalizedPlace)
    assert result.name == "Slonim"
    assert result.country_code == "BY"
    assert result.confidence == 0.95
    # Промпт был отрендерен и отправлен.
    assert mock_anthropic_client.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_normalize_uses_structured_output(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """Запрос идёт с json_schema output_config (гарантия валидного JSON)."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"name": "Vilnius", "country_code": "LT", "historical_period": null, "confidence": 0.9}'
    )

    await normalize_place_name("Wilno", client=mock_anthropic_client)

    call_kwargs = mock_anthropic_client.messages.create.await_args.kwargs
    assert "output_config" in call_kwargs
    fmt = call_kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "name" in fmt["schema"]["properties"]
    assert "country_code" in fmt["schema"]["properties"]
    assert "confidence" in fmt["schema"]["properties"]


@pytest.mark.asyncio
async def test_normalize_handles_null_country_code(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """Не-сопоставимое место (Wild Fields) → country_code=None."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"name": "Дикое Поле", "country_code": null, '
        '"historical_period": "Steppe (16th century)", "confidence": 0.4}'
    )

    result = await normalize_place_name(
        "Wild Fields",
        client=mock_anthropic_client,
    )
    assert result.country_code is None
    assert result.confidence == 0.4


@pytest.mark.asyncio
async def test_normalize_default_model_is_sonnet_46(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """Default — claude-sonnet-4-6 (см. ADR-0030)."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"name": "Lviv", "country_code": "UA", "historical_period": null, "confidence": 0.9}'
    )

    await normalize_place_name("Lwów", client=mock_anthropic_client)
    call_kwargs = mock_anthropic_client.messages.create.await_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_normalize_thinking_disabled(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """Канонизация места — short-form, thinking не нужен (cost optimization)."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"name": "Lviv", "country_code": "UA", "historical_period": null, "confidence": 0.9}'
    )

    await normalize_place_name("Lwów", client=mock_anthropic_client)
    call_kwargs = mock_anthropic_client.messages.create.await_args.kwargs
    assert call_kwargs["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_normalize_renders_context_in_prompt(
    mock_anthropic_client: MagicMock,
    make_response: Callable[[str], MagicMock],
) -> None:
    """`context` json-сериализуется и попадает в текст промпта."""
    mock_anthropic_client.messages.create.return_value = make_response(
        '{"name": "Slonim", "country_code": "BY", "historical_period": null, "confidence": 0.9}'
    )

    await normalize_place_name(
        "Slonim",
        context={"person_birth_year": 1880},
        client=mock_anthropic_client,
    )
    call_kwargs = mock_anthropic_client.messages.create.await_args.kwargs
    user_text = call_kwargs["messages"][0]["content"]
    assert "person_birth_year" in user_text
    assert "1880" in user_text
    assert "Slonim" in user_text
