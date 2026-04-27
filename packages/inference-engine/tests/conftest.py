"""Pytest fixtures for inference-engine.

Главное — autouse фикстура ``_clear_inference_registry``, которая
сбрасывает module-level registry между тестами. Без неё тесты,
регистрирующие rules, влияли бы друг на друга.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from inference_engine import clear_registry


@pytest.fixture(autouse=True)
def _clear_inference_registry() -> Iterator[None]:
    """Сбросить registry до и после каждого теста."""
    clear_registry()
    yield
    clear_registry()
