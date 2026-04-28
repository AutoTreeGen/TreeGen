"""Тесты ``claude_client`` фабрики."""

from __future__ import annotations

import pytest
from anthropic import AsyncAnthropic
from llm_services import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    MissingApiKeyError,
    claude_client,
)


def test_default_model_is_sonnet_46() -> None:
    """Default model — claude-sonnet-4-6 (зафиксировано в client.py + ADR-0030)."""
    assert DEFAULT_MODEL == "claude-sonnet-4-6"


def test_default_timeout_and_retries_are_sane() -> None:
    assert DEFAULT_TIMEOUT_SECONDS > 0
    assert DEFAULT_MAX_RETRIES >= 0


def test_explicit_api_key_creates_client() -> None:
    client = claude_client(api_key="sk-test")
    assert isinstance(client, AsyncAnthropic)


def test_env_api_key_is_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    client = claude_client()
    assert isinstance(client, AsyncAnthropic)


def test_missing_key_raises_specific_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        claude_client()


def test_empty_string_key_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустая строка в env — это «не задан», не валидный ключ."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(MissingApiKeyError):
        claude_client()
