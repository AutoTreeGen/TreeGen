"""Phase 13.1b — observability init не должен падать при пустом ``SENTRY_DSN``.

Просто загрузка ``parser_service.main`` уже выполняет ``setup_logging`` +
``setup_sentry``. Тест ловит регрессии вида «забыли try/except в lazy
import sentry_sdk» — без них сервис не поднимется в окружении без DSN.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_sentry_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гарантируем no-op путь — даже если у разработчика SENTRY_DSN в env."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)


def test_main_module_imports_without_sentry_dsn() -> None:
    """``import parser_service.main`` отрабатывает с пустым DSN."""
    import importlib

    import parser_service.main as main_module

    importlib.reload(main_module)
    assert main_module.app is not None


def test_setup_sentry_returns_false_with_empty_dsn() -> None:
    """``setup_sentry`` no-op без DSN — возвращает ``False`` и не падает."""
    from shared_models.observability import setup_sentry

    assert os.environ.get("SENTRY_DSN", "") == ""
    assert setup_sentry(service_name="parser-service") is False
