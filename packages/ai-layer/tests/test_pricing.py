"""Тесты ``pricing.estimate_cost_usd`` (Phase 10.1)."""

from __future__ import annotations

import pytest
from ai_layer.pricing import PRICING, estimate_cost_usd


def test_estimate_sonnet_simple() -> None:
    """3000 in × $3/MTok + 500 out × $15/MTok = $0.009 + $0.0075 = $0.0165."""
    cost = estimate_cost_usd("claude-sonnet-4-6", 3_000, 500)
    assert cost == pytest.approx(0.0165, abs=1e-6)


def test_estimate_haiku_cheaper_than_sonnet() -> None:
    cheap = estimate_cost_usd("claude-haiku-4-5-20251001", 3_000, 500)
    base = estimate_cost_usd("claude-sonnet-4-6", 3_000, 500)
    assert cheap < base


def test_estimate_opus_pricier_than_sonnet() -> None:
    expensive = estimate_cost_usd("claude-opus-4-7", 3_000, 500)
    base = estimate_cost_usd("claude-sonnet-4-6", 3_000, 500)
    assert expensive > base


def test_unknown_model_falls_back_to_sonnet_pricing() -> None:
    """Неизвестная модель → fallback (Sonnet 4.6), а не KeyError."""
    fallback = estimate_cost_usd("claude-future-9000", 1_000, 100)
    sonnet = estimate_cost_usd("claude-sonnet-4-6", 1_000, 100)
    assert fallback == sonnet


def test_zero_tokens_zero_cost() -> None:
    assert estimate_cost_usd("claude-sonnet-4-6", 0, 0) == 0.0


def test_pricing_table_keys_match_documented_models() -> None:
    """Smoke: registry содержит наши baseline-модели."""
    assert "claude-sonnet-4-6" in PRICING
    assert "claude-opus-4-7" in PRICING
