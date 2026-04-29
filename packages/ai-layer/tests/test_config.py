"""Тесты ``AILayerConfig`` и парсинга ENV."""

from __future__ import annotations

from ai_layer.config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_VOYAGE_MODEL,
    AILayerConfig,
)


def test_from_env_defaults() -> None:
    """Пустой env → enabled=false, ключи=None, модели — defaults."""
    config = AILayerConfig.from_env(env={})
    assert config.enabled is False
    assert config.anthropic_api_key is None
    assert config.voyage_api_key is None
    assert config.anthropic_model == DEFAULT_ANTHROPIC_MODEL
    assert config.voyage_model == DEFAULT_VOYAGE_MODEL


def test_from_env_full() -> None:
    """Все переменные переданы — все поля заполнены."""
    config = AILayerConfig.from_env(
        env={
            "AI_LAYER_ENABLED": "true",
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "ANTHROPIC_MODEL": "claude-opus-4-7",
            "VOYAGE_API_KEY": "pa-yyy",
            "VOYAGE_MODEL": "voyage-3-large",
        }
    )
    assert config.enabled is True
    assert config.anthropic_api_key == "sk-ant-xxx"
    assert config.anthropic_model == "claude-opus-4-7"
    assert config.voyage_api_key == "pa-yyy"
    assert config.voyage_model == "voyage-3-large"


def test_from_env_bool_flag_variants() -> None:
    """``AI_LAYER_ENABLED`` принимает 1/true/yes/on (case-insensitive)."""
    for value in ("1", "true", "TRUE", "yes", "On"):
        config = AILayerConfig.from_env(env={"AI_LAYER_ENABLED": value})
        assert config.enabled is True, f"failed for {value!r}"
    for value in ("0", "false", "no", "", "anything-else"):
        config = AILayerConfig.from_env(env={"AI_LAYER_ENABLED": value})
        assert config.enabled is False, f"failed for {value!r}"


def test_from_env_empty_keys_normalized_to_none() -> None:
    """Пустая строка ключа → None (распространённый случай ``ANTHROPIC_API_KEY=``)."""
    config = AILayerConfig.from_env(env={"ANTHROPIC_API_KEY": "", "VOYAGE_API_KEY": ""})
    assert config.anthropic_api_key is None
    assert config.voyage_api_key is None
