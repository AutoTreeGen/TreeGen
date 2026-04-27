"""Smoke-тесты для FamilySearchAuth (Phase 5.0 skeleton).

Полные тесты PKCE flow — в Task 3 PR.
"""

from __future__ import annotations

from familysearch_client import FamilySearchAuth, FamilySearchConfig


def test_auth_imports_and_constructs() -> None:
    """FamilySearchAuth конструируется с дефолтным sandbox-конфигом."""
    auth = FamilySearchAuth(client_id="test-app-key")
    assert auth.client_id == "test-app-key"
    assert auth.config.environment == "sandbox"


def test_auth_repr_does_not_leak_client_id() -> None:
    """repr() не содержит client_id (минимум — не падает в логах)."""
    auth = FamilySearchAuth(client_id="should-not-appear")
    assert "should-not-appear" not in repr(auth)


def test_auth_accepts_explicit_production_config() -> None:
    """Production endpoints конструируются явным вызовом."""
    config = FamilySearchConfig.production()
    auth = FamilySearchAuth(client_id="prod-key", config=config)
    assert auth.config.environment == "production"
    assert auth.config.api_base_url == "https://api.familysearch.org"
