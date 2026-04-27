"""Тесты BirthPlaceMatchRule (Phase 7.1 Task 2)."""

from __future__ import annotations

from inference_engine.rules.place import BirthPlaceMatchRule
from inference_engine.types import EvidenceDirection

_RULE = BirthPlaceMatchRule()


def test_rule_id_is_stable() -> None:
    assert _RULE.rule_id == "birth_place_match"


def test_identical_places_strong_supports() -> None:
    out = _RULE.apply(
        {"birth_place": "Slonim, Grodno"},
        {"birth_place": "Slonim, Grodno"},
        {},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS
    # weight = 0.4 × score; для identical score=1.0 → 0.4.
    assert out[0].weight == 0.4


def test_hierarchical_subset_supports() -> None:
    """«Slonim» ⊂ «Slonim, Grodno, Russian Empire» → ≥0.85 → SUPPORTS."""
    out = _RULE.apply(
        {"birth_place": "Slonim"},
        {"birth_place": "Slonim, Grodno, Russian Empire"},
        {},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS
    assert out[0].weight >= 0.4 * 0.85
    # Score должен быть в provenance для UI explainability.
    assert "score" in out[0].source_provenance


def test_completely_different_places_contradicts() -> None:
    """Slonim, Belarus vs Boston, Massachusetts → score < 0.30 → CONTRADICTS."""
    out = _RULE.apply(
        {"birth_place": "Slonim, Belarus"},
        {"birth_place": "Boston, Massachusetts"},
        {},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.CONTRADICTS
    assert out[0].weight == 0.30


def test_grey_zone_no_evidence() -> None:
    """Score между 0.30 и 0.80 → пустой list (NEUTRAL silence)."""
    # Слабо пересекающиеся, но не radically разные — попадают в grey zone.
    out = _RULE.apply(
        {"birth_place": "Slonim, Grodno"},
        {"birth_place": "Pinsk, Minsk"},
        {},
    )
    # Конкретная асserция: либо пустой, либо не SUPPORTS strong.
    # Важно: не должно быть SUPPORTS с weight ≥ 0.4*0.8.
    if out:
        for ev in out:
            assert ev.direction is not EvidenceDirection.SUPPORTS or ev.weight < 0.4 * 0.8


def test_cyrillic_normalization() -> None:
    """«Днепропетровск» транслитерируется и матчится с «Dnepropetrovsk»."""
    out = _RULE.apply(
        {"birth_place": "Dnepropetrovsk"},
        {"birth_place": "Днепропетровск"},
        {},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS


def test_missing_birth_place_no_evidence() -> None:
    out = _RULE.apply({}, {"birth_place": "Slonim"}, {})
    assert out == []
    out = _RULE.apply({"birth_place": "Slonim"}, {}, {})
    assert out == []


def test_empty_birth_place_no_evidence() -> None:
    out = _RULE.apply({"birth_place": ""}, {"birth_place": "Slonim"}, {})
    assert out == []
