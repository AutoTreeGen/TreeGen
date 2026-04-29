"""Pytest-фикстуры для ai-layer.

Все внешние API замоканы: реальные вызовы Anthropic / Voyage запрещены
в CI. ``enabled_config`` всегда включает ``AI_LAYER_ENABLED=true``,
чтобы тесты могли упражнять success-path; disabled-сценарии тестируются
отдельной фикстурой.

Fake-реализации SDK живут в ``tests/_fakes.py``. Мы НЕ добавляем
``tests/__init__.py``, потому что в monorepo'е несколько пакетов с
одноимёнными ``tests/conftest.py`` под --import-mode=importlib коллизятся
по имени модуля ``tests.conftest`` (см. dna-analysis tests). Вместо этого
явно подкидываем директорию в sys.path — conftest загружается до тестов,
поэтому ``from _fakes import ...`` в test-модулях резолвится корректно.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from ai_layer.config import AILayerConfig

_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# Импорт после sys.path-инъекции — намеренно (см. модульный docstring).
from _fakes import FakeAnthropic, FakeMessage, FakeVoyage, FakeVoyageResult  # noqa: E402


@pytest.fixture
def enabled_config() -> AILayerConfig:
    """Конфиг с enabled=true и фейковыми ключами (для success-path тестов)."""
    return AILayerConfig(
        enabled=True,
        anthropic_api_key="test-anthropic-key",
        anthropic_model="claude-sonnet-4-6",
        voyage_api_key="test-voyage-key",
        voyage_model="voyage-3",
    )


@pytest.fixture
def disabled_config() -> AILayerConfig:
    """Конфиг с enabled=false — для негативных тестов kill-switch."""
    return AILayerConfig(enabled=False)


@pytest.fixture
def make_fake_anthropic() -> Callable[[Callable[..., FakeMessage]], FakeAnthropic]:
    """Фабрика stub'ов: тест передаёт responder-функцию."""

    def _factory(responder: Callable[..., FakeMessage]) -> FakeAnthropic:
        return FakeAnthropic(responder)

    return _factory


@pytest.fixture
def make_fake_voyage() -> Callable[[Callable[..., FakeVoyageResult]], FakeVoyage]:
    def _factory(responder: Callable[..., FakeVoyageResult]) -> FakeVoyage:
        return FakeVoyage(responder)

    return _factory
