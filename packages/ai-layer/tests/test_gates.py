"""Тесты ``ai_layer.gates`` — kill-switch helpers."""

from __future__ import annotations

import pytest
from ai_layer.config import AILayerConfig, AILayerDisabledError
from ai_layer.gates import ensure_ai_layer_enabled, make_ai_layer_gate


def test_ensure_ai_layer_enabled_allows_when_true() -> None:
    config = AILayerConfig(enabled=True)
    ensure_ai_layer_enabled(config)  # no raise


def test_ensure_ai_layer_enabled_raises_when_false() -> None:
    config = AILayerConfig(enabled=False)
    with pytest.raises(AILayerDisabledError, match="AI layer is disabled"):
        ensure_ai_layer_enabled(config)


def test_make_ai_layer_gate_factory() -> None:
    enabled_cfg = AILayerConfig(enabled=True)
    disabled_cfg = AILayerConfig(enabled=False)

    enabled_gate = make_ai_layer_gate(lambda: enabled_cfg)
    enabled_gate()  # no raise

    disabled_gate = make_ai_layer_gate(lambda: disabled_cfg)
    with pytest.raises(AILayerDisabledError):
        disabled_gate()
