"""Тесты загрузчика промптов."""

from __future__ import annotations

import pytest
from llm_services.prompts import load_prompt


def test_load_place_normalization_returns_versioned_template() -> None:
    version, body = load_prompt("place_normalization")
    assert version.startswith("v")  # "v1", "v2", ...
    assert "place" in body.lower()
    assert "{raw}" in body
    assert "{context}" in body


def test_load_name_disambiguation_returns_versioned_template() -> None:
    version, body = load_prompt("name_disambiguation")
    assert version.startswith("v")
    assert "{variants}" in body


def test_unknown_prompt_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist")


def test_load_prompt_is_cached() -> None:
    """LRU-cache: повторные вызовы не читают файл заново."""
    a = load_prompt("place_normalization")
    b = load_prompt("place_normalization")
    # Тот же кортеж по ссылке — значит cache hit.
    assert a is b
