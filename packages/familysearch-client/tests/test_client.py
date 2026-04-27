"""Smoke-тесты для FamilySearchClient (Phase 5.0 skeleton).

Полная реализация ``get_person`` и mock-тесты — в Task 4 PR.
"""

from __future__ import annotations

import pytest
from familysearch_client import (
    AuthError,
    ClientError,
    FamilySearchClient,
    FamilySearchConfig,
    FamilySearchError,
    FsGender,
    FsPerson,
    NotFoundError,
    RateLimitError,
    ServerError,
)


def test_client_imports_and_constructs() -> None:
    """FamilySearchClient конструируется с access_token и sandbox-дефолтом."""
    client = FamilySearchClient(access_token="test-token")
    assert client.config.environment == "sandbox"


def test_client_repr_does_not_leak_token() -> None:
    """repr() не содержит access_token."""
    client = FamilySearchClient(access_token="super-secret-token")
    assert "super-secret-token" not in repr(client)


@pytest.mark.asyncio
async def test_client_supports_async_context_manager() -> None:
    """async with … работает (на Phase 5.0 cleanup пустой)."""
    config = FamilySearchConfig.sandbox()
    async with FamilySearchClient(access_token="t", config=config) as client:
        assert client.config.environment == "sandbox"


def test_error_hierarchy() -> None:
    """Все специфичные ошибки наследуются от FamilySearchError."""
    for err_cls in (AuthError, NotFoundError, RateLimitError, ServerError, ClientError):
        assert issubclass(err_cls, FamilySearchError)


def test_rate_limit_error_carries_retry_after() -> None:
    """RateLimitError.retry_after доступен после конструирования."""
    err = RateLimitError("hit 429", retry_after=12.5)
    assert err.retry_after == 12.5
    assert "429" in str(err)


def test_fs_person_display_name_fallbacks_to_id() -> None:
    """Без имён display_name возвращает Person ID."""
    person = FsPerson(id="KW7S-VQJ", gender=FsGender.UNKNOWN)
    assert person.display_name == "KW7S-VQJ"
