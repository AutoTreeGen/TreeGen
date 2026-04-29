"""Тесты ``AnthropicClient`` со stub'ом SDK."""

from __future__ import annotations

import pytest
from _fakes import FakeAnthropic, FakeMessage, FakeTextBlock, FakeUsage
from ai_layer.clients.anthropic_client import AnthropicClient
from ai_layer.config import AILayerConfig, AILayerConfigError, AILayerDisabledError
from pydantic import BaseModel, ValidationError


class _Greeting(BaseModel):
    greeting: str


@pytest.mark.asyncio
async def test_complete_structured_happy_path(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Успешный путь: SDK возвращает JSON, обёртка парсит в Pydantic."""

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(
            content=[FakeTextBlock(text='{"greeting": "shalom"}')],
            model="claude-sonnet-4-6",
            usage=FakeUsage(input_tokens=12, output_tokens=4),
        )

    fake: FakeAnthropic = make_fake_anthropic(responder)
    client = AnthropicClient(enabled_config, client=fake)

    result = await client.complete_structured(
        system="sys",
        user="usr",
        response_model=_Greeting,
    )

    assert result.parsed == _Greeting(greeting="shalom")
    assert result.input_tokens == 12
    assert result.output_tokens == 4
    assert result.stop_reason == "end_turn"
    assert fake.messages.calls[0]["system"] == "sys"
    assert fake.messages.calls[0]["messages"] == [{"role": "user", "content": "usr"}]


@pytest.mark.asyncio
async def test_disabled_config_blocks_call(
    disabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """``AI_LAYER_ENABLED=false`` → AILayerDisabledError, SDK не вызывается."""

    def responder(**_: object) -> FakeMessage:
        msg = "should not be called"
        raise AssertionError(msg)

    fake = make_fake_anthropic(responder)
    client = AnthropicClient(disabled_config, client=fake)

    with pytest.raises(AILayerDisabledError):
        await client.complete_structured(
            system="sys",
            user="usr",
            response_model=_Greeting,
        )
    assert fake.messages.calls == []


@pytest.mark.asyncio
async def test_missing_api_key_without_injected_client(
    enabled_config: AILayerConfig,
) -> None:
    """``enabled=true`` + пустой API key + injected client отсутствует → конфигурационная ошибка."""
    config = AILayerConfig(enabled=True, anthropic_api_key=None)
    client = AnthropicClient(config, client=None)
    with pytest.raises(AILayerConfigError):
        await client.complete_structured(
            system="sys",
            user="usr",
            response_model=_Greeting,
        )
    # Ключ пустой → ошибка ловится до сетевого вызова, enabled_config-фикстура
    # тут не нужна, но импорт оставлен для consistency сигнатуры; явно используем,
    # чтобы линтер не ругался на unused.
    assert enabled_config.enabled is True


@pytest.mark.asyncio
async def test_invalid_json_raises_validation_error(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """LLM вернул не-JSON → Pydantic ValidationError; caller отвечает за обработку."""

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text="not-json")])

    fake = make_fake_anthropic(responder)
    client = AnthropicClient(enabled_config, client=fake)

    with pytest.raises(ValidationError):
        await client.complete_structured(
            system="sys",
            user="usr",
            response_model=_Greeting,
        )


@pytest.mark.asyncio
async def test_empty_content_raises_value_error(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """SDK вернул пустой content-list → явная ValueError, без silent-fail."""

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[])

    client = AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    with pytest.raises(ValueError, match="empty content"):
        await client.complete_structured(
            system="sys",
            user="usr",
            response_model=_Greeting,
        )


@pytest.mark.asyncio
async def test_no_text_blocks_raises_value_error(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Content-list без text-блоков → ValueError."""

    class _NonTextBlock:
        type = "tool_use"

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[_NonTextBlock()])

    client = AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    with pytest.raises(ValueError, match="no text blocks"):
        await client.complete_structured(
            system="sys",
            user="usr",
            response_model=_Greeting,
        )


@pytest.mark.asyncio
async def test_dict_text_blocks_supported(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """SDK иногда возвращает блок как dict — тоже поддерживаем."""

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[{"type": "text", "text": '{"greeting": "hi"}'}])

    client = AnthropicClient(enabled_config, client=make_fake_anthropic(responder))
    result = await client.complete_structured(
        system="sys",
        user="usr",
        response_model=_Greeting,
    )
    assert result.parsed.greeting == "hi"


@pytest.mark.asyncio
async def test_model_override_takes_precedence(
    enabled_config: AILayerConfig,
    make_fake_anthropic,
) -> None:
    """Per-call ``model`` перекрывает дефолт из конфига."""

    def responder(**_: object) -> FakeMessage:
        return FakeMessage(content=[FakeTextBlock(text='{"greeting": "x"}')])

    fake = make_fake_anthropic(responder)
    client = AnthropicClient(enabled_config, client=fake)
    await client.complete_structured(
        system="s",
        user="u",
        response_model=_Greeting,
        model="claude-opus-4-7",
    )
    assert fake.messages.calls[0]["model"] == "claude-opus-4-7"
