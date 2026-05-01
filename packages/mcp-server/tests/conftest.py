"""Shared fixtures для treegen-mcp тестов."""

from __future__ import annotations

import pytest
from treegen_mcp.auth import ApiCredentials
from treegen_mcp.config import TreeGenConfig


@pytest.fixture
def config() -> TreeGenConfig:
    """Тестовый конфиг с фиксированным API URL."""
    return TreeGenConfig(api_url="https://api.test.example", timeout_seconds=5.0)


@pytest.fixture
def credentials() -> ApiCredentials:
    """Фиктивные API-credentials."""
    return ApiCredentials(api_key="atg_test_secret_key_xyz")
