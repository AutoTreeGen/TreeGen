"""Fake-implementations внешних SDK для тестов.

Вынесено в отдельный модуль (а не conftest.py), потому что
``--import-mode=importlib`` (см. pyproject) не добавляет ``tests/``
в sys.path. Импорт ``from ._fakes import ...`` через относительный
путь работает в любом режиме.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class FakeUsage:
    """Эмуляция ``Message.usage`` SDK-объекта."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class FakeTextBlock:
    """Эмуляция ``TextBlock`` из anthropic SDK."""

    text: str
    type: str = "text"


@dataclass
class FakeMessage:
    """Эмуляция ``Message`` SDK-объекта."""

    content: list[Any]
    model: str = "claude-sonnet-4-6"
    stop_reason: str | None = "end_turn"
    usage: FakeUsage | None = None


class FakeMessages:
    """Эмуляция ``client.messages`` namespace."""

    def __init__(self, responder: Callable[..., FakeMessage]) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        return self._responder(**kwargs)


class FakeAnthropic:
    """Минимальный ``AsyncAnthropic``-stub: только ``.messages.create``."""

    def __init__(self, responder: Callable[..., FakeMessage]) -> None:
        self.messages = FakeMessages(responder)


@dataclass
class FakeVoyageResult:
    """Эмуляция voyageai.AsyncClient.embed-result."""

    embeddings: list[list[float]]


class FakeVoyage:
    """Минимальный ``voyageai.AsyncClient``-stub: только ``.embed``."""

    def __init__(self, responder: Callable[..., FakeVoyageResult]) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def embed(
        self,
        texts: list[str],
        *,
        model: str,
        input_type: str,
    ) -> FakeVoyageResult:
        self.calls.append({"texts": list(texts), "model": model, "input_type": input_type})
        return self._responder(texts=texts, model=model, input_type=input_type)
