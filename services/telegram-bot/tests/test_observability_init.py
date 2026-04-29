"""Phase 13.1b — observability init не должен падать при пустом ``SENTRY_DSN``."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_sentry_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)


def test_main_module_imports_without_sentry_dsn() -> None:
    import importlib

    import telegram_bot.main as main_module

    importlib.reload(main_module)
    assert main_module.app is not None


def test_setup_sentry_returns_false_with_empty_dsn() -> None:
    from shared_models.observability import setup_sentry

    assert os.environ.get("SENTRY_DSN", "") == ""
    assert setup_sentry(service_name="telegram-bot") is False
