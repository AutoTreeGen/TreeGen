"""Shared fixtures для llm-services тестов.

Главное — НЕ ходим в реальный Anthropic API в CI. Все тесты, кроме
explicit ``integration`` маркера, мокают ``AsyncAnthropic.messages.create``.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_response(payload: str) -> MagicMock:
    """Создать mock-объект ``Message`` с одним text-блоком."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = payload
    response = MagicMock()
    response.content = [text_block]
    return response


# Type-aliases для fixture-сигнатур: импортируются в тестах для type-hint'ов
# параметров. Сами объекты — обычные MagicMock / callable factory.
MockAnthropicClient = MagicMock
MakeResponse = Callable[[str], MagicMock]


@pytest.fixture
def mock_anthropic_client() -> MockAnthropicClient:
    """Mock ``AsyncAnthropic`` с настраиваемым ``messages.create`` ответом.

    Тест присваивает ``client.messages.create.return_value = _make_response(...)``
    или использует side_effect для последовательности ответов.
    """
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


@pytest.fixture
def make_response() -> MakeResponse:
    """Фабрика mock-ответа от Claude API (один text-блок с заданным JSON-телом)."""
    return _make_response
