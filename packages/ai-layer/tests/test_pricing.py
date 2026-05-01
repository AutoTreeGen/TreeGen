"""Тесты ``pricing.*`` helpers (Phase 10.1 + 10.2b + 10.9a)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from ai_layer.pricing import (
    PRICING,
    WHISPER_PRICING_PER_MIN_USD,
    estimate_cost_usd,
    estimate_extraction_cost_usd,
    estimate_input_tokens_from_image,
    estimate_input_tokens_from_text,
    estimate_whisper_cost_usd,
)


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


# Phase 10.2b — pre-flight cost estimation helpers.


def test_estimate_input_tokens_from_text_grows_with_length() -> None:
    """Длиннее input → больше estimated input tokens (после prompt-overhead'а)."""
    short = estimate_input_tokens_from_text(text_length_chars=100)
    long = estimate_input_tokens_from_text(text_length_chars=10_000)
    assert long > short
    # Floor — prompt overhead (~1200 tokens) даже на пустом input'е.
    assert estimate_input_tokens_from_text(text_length_chars=0) >= 1000


def test_estimate_input_tokens_from_image_includes_image_ceiling() -> None:
    """Vision-вызов без OCR-hint'а — image-tokens ceiling + prompt overhead."""
    no_hint = estimate_input_tokens_from_image()
    # ≥ 2200 image-tokens + 1200 prompt overhead = 3400 минимум.
    assert no_hint >= 3000

    with_hint = estimate_input_tokens_from_image(ocr_text_hint_length_chars=500)
    assert with_hint > no_hint


def test_estimate_extraction_cost_usd_respects_input_and_output() -> None:
    cheap = estimate_extraction_cost_usd(
        model="claude-sonnet-4-6",
        estimated_input_tokens=1_000,
        max_output_tokens=500,
    )
    expensive = estimate_extraction_cost_usd(
        model="claude-sonnet-4-6",
        estimated_input_tokens=10_000,
        max_output_tokens=4_000,
    )
    assert expensive > cheap
    assert cheap > 0


def test_estimate_extraction_cost_below_default_05_for_typical_text() -> None:
    """Sanity: типичный 4k-char документ помещается в default $0.50 cap."""
    estimated_input = estimate_input_tokens_from_text(text_length_chars=4_000)
    cost = estimate_extraction_cost_usd(
        model="claude-sonnet-4-6",
        estimated_input_tokens=estimated_input,
        max_output_tokens=4096,
    )
    assert cost < 0.50


# Phase 10.9a — Whisper STT pricing helpers.


def test_whisper_pricing_table_contains_whisper_1() -> None:
    assert WHISPER_PRICING_PER_MIN_USD["whisper-1"] == Decimal("0.006")


def test_estimate_whisper_cost_full_minute() -> None:
    """60 сек × $0.006/min = $0.006 ровно."""
    assert estimate_whisper_cost_usd(60) == Decimal("0.006000")


def test_estimate_whisper_cost_half_minute() -> None:
    """30 сек × $0.006/min = $0.003."""
    assert estimate_whisper_cost_usd(30) == Decimal("0.003000")


def test_estimate_whisper_cost_zero_duration() -> None:
    assert estimate_whisper_cost_usd(0) == Decimal("0.000000")


def test_estimate_whisper_cost_negative_clamped_to_zero() -> None:
    """Повреждённое аудио с отрицательной длительностью не должно билиться."""
    assert estimate_whisper_cost_usd(-5.0) == Decimal("0.000000")


def test_estimate_whisper_cost_returns_decimal() -> None:
    """Тип возвращаемого значения — :class:`Decimal`, не float (precision)."""
    assert isinstance(estimate_whisper_cost_usd(45.5), Decimal)


def test_estimate_whisper_cost_unknown_model_raises() -> None:
    """Неизвестная модель → KeyError (silent fallback здесь запрещён —
    биллить по неизвестному тарифу нельзя)."""
    with pytest.raises(KeyError):
        estimate_whisper_cost_usd(60, model="whisper-future-x")


def test_estimate_whisper_cost_quantum_six_places() -> None:
    """Округление — 6 знаков после запятой (совпадает с estimate_cost_usd)."""
    cost = estimate_whisper_cost_usd(7)  # 7/60 * 0.006 = 0.0007 ровно
    assert cost == Decimal("0.000700")
    # 1 секунда: 0.006 / 60 = 0.0001 — точно 4 знака, но quantize до 6.
    assert estimate_whisper_cost_usd(1) == Decimal("0.000100")
