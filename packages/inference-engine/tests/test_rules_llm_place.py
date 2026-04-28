"""Тесты LlmPlaceMatchRule (Phase 10.0).

LLM-rule вызывается через injected sync-callable, поэтому тесты не
требуют ни Anthropic API, ни asyncio-bridge — простой mock-callable.
"""

from __future__ import annotations

import pytest
from inference_engine import register_rule
from inference_engine.rules.llm_place import LlmPlaceMatchRule
from inference_engine.types import EvidenceDirection
from llm_services import NormalizedPlace


def _norm(name: str, country: str | None, confidence: float = 0.9) -> NormalizedPlace:
    return NormalizedPlace(
        name=name,
        country_code=country,
        historical_period=None,
        confidence=confidence,
    )


def test_rule_id_is_stable() -> None:
    rule = LlmPlaceMatchRule()
    assert rule.rule_id == "llm_place_match"


def test_rule_can_be_registered() -> None:
    """Rule должен проходить InferenceRule Protocol-проверку и регистрироваться."""
    rule = LlmPlaceMatchRule()
    register_rule(rule)  # не должно бросать


def test_no_normalizer_returns_no_evidence() -> None:
    """Default normalizer=None → zero-cost mode (для окружений без API key)."""
    rule = LlmPlaceMatchRule(normalizer=None)
    out = rule.apply(
        {"birth_place": "Slonim, Russian Empire"},
        {"birth_place": "Slonim, BLR"},
        {},
    )
    assert out == []


def test_missing_birth_place_no_evidence() -> None:
    rule = LlmPlaceMatchRule(normalizer=lambda _raw: _norm("X", "BY"))
    assert rule.apply({}, {"birth_place": "Slonim"}, {}) == []
    assert rule.apply({"birth_place": "Slonim"}, {}, {}) == []


def test_slam_dunk_match_above_gray_zone_skips_llm() -> None:
    """Score ≥ 0.70 → не вызываем LLM, BirthPlaceMatchRule уже сказал SUPPORTS.

    «Slonim, Grodno» vs «Slonim, Grodno» — score = 1.0, identical.
    """
    calls: list[str] = []

    def normalizer(raw: str) -> NormalizedPlace:
        calls.append(raw)
        return _norm("Slonim", "BY")

    rule = LlmPlaceMatchRule(normalizer=normalizer)
    out = rule.apply(
        {"birth_place": "Slonim, Grodno"},
        {"birth_place": "Slonim, Grodno"},
        {},
    )
    assert out == []
    assert calls == [], "LLM не должен вызываться при slam-dunk SUPPORTS"


def test_slam_dunk_contradict_below_gray_zone_skips_llm() -> None:
    """Score < 0.40 → не вызываем LLM, BirthPlaceMatchRule уже сказал CONTRADICTS."""
    calls: list[str] = []

    def normalizer(raw: str) -> NormalizedPlace:
        calls.append(raw)
        return _norm("X", "BY")

    rule = LlmPlaceMatchRule(normalizer=normalizer)
    out = rule.apply(
        {"birth_place": "Slonim, Belarus"},
        {"birth_place": "Boston, Massachusetts"},
        {},
    )
    assert out == []
    assert calls == [], "LLM не должен вызываться при slam-dunk CONTRADICTS"


# Известные gray-zone пары (fuzzy-score ∈ [0.40, 0.70]) — measured
# через place_match_score + transliterate_cyrillic в ходе разработки.
# При изменении формулы scoring эти константы могут потребовать обновления.
_GRAY_ZONE_PAIR_SAME = ("Slonim, Grodno", "Slonim, Russian Empire")  # ~0.667
_GRAY_ZONE_PAIR_DIFFERENT = ("Brest, Belarus", "Brest, France")  # ~0.632


def test_gray_zone_same_canonical_supports() -> None:
    """Gray-zone score + LLM канонизирует к одному name+country → SUPPORTS."""

    def normalizer(_raw: str) -> NormalizedPlace:
        return _norm("Slonim", "BY", confidence=0.95)

    rule = LlmPlaceMatchRule(normalizer=normalizer)
    a, b = _GRAY_ZONE_PAIR_SAME
    out = rule.apply({"birth_place": a}, {"birth_place": b}, {})

    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS
    assert out[0].rule_id == "llm_place_match"
    # weight = 0.30 × confidence
    assert out[0].weight == pytest.approx(0.30 * 0.95)
    assert out[0].source_provenance["package"] == "llm-services"
    assert "rule_based_score" in out[0].source_provenance


def test_gray_zone_different_country_contradicts() -> None:
    """Gray-zone + LLM канонизирует к разным странам → CONTRADICTS."""

    def normalizer(raw: str) -> NormalizedPlace:
        if "France" in raw:
            return _norm("Brest", "FR", confidence=0.9)
        return _norm("Brest", "BY", confidence=0.9)

    rule = LlmPlaceMatchRule(normalizer=normalizer)
    a, b = _GRAY_ZONE_PAIR_DIFFERENT
    out = rule.apply({"birth_place": a}, {"birth_place": b}, {})

    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.CONTRADICTS
    assert out[0].weight == pytest.approx(0.25 * 0.9)


def test_low_llm_confidence_no_evidence() -> None:
    """LLM confidence < 0.50 → шум, evidence не выдаём."""

    def normalizer(_raw: str) -> NormalizedPlace:
        return _norm("Slonim", "BY", confidence=0.3)

    rule = LlmPlaceMatchRule(normalizer=normalizer)
    a, b = _GRAY_ZONE_PAIR_SAME
    out = rule.apply({"birth_place": a}, {"birth_place": b}, {})
    assert out == []


def test_normalizer_called_exactly_once_per_subject_in_gray_zone() -> None:
    """Cost-control: ровно 2 вызова normalizer (по одному на каждое место)."""
    calls: list[str] = []

    def normalizer(raw: str) -> NormalizedPlace:
        calls.append(raw)
        return _norm("Slonim", "BY", confidence=0.9)

    rule = LlmPlaceMatchRule(normalizer=normalizer)
    a, b = _GRAY_ZONE_PAIR_SAME
    rule.apply({"birth_place": a}, {"birth_place": b}, {})
    # Gray-zone пара → ровно 2 вызова (по одному на каждое место).
    assert len(calls) == 2
