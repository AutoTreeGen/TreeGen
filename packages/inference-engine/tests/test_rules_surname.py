"""Тесты SurnameMatchRule (Phase 7.1 Task 1)."""

from __future__ import annotations

from inference_engine.rules.surname import SurnameMatchRule
from inference_engine.types import EvidenceDirection

_RULE = SurnameMatchRule()


def test_rule_id_is_stable() -> None:
    """rule_id фиксирован — менять его ломает persisted Evidence (Phase 7.2+)."""
    assert _RULE.rule_id == "surname_dm_match"


def test_identical_surnames_produce_supports_evidence() -> None:
    out = _RULE.apply({"surname": "Smith"}, {"surname": "Smith"}, {})
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS
    assert out[0].weight == 0.5


def test_zhitnitzky_variants_match() -> None:
    """Главный success signal Phase 7.1 — латинские транслитерации."""
    out = _RULE.apply(
        {"surname": "Zhitnitzky"},
        {"surname": "Zhytnicki"},
        {},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS
    assert "Daitch-Mokotoff bucket overlap" in out[0].observation


def test_cyrillic_to_latin_transliteration_match() -> None:
    """Кириллица должна транслитерироваться → bucket overlap с латиницей."""
    out = _RULE.apply(
        {"surname": "Zhitnitzky"},
        {"surname": "Житницкий"},
        {},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS


def test_distinct_surnames_no_evidence() -> None:
    """Совершенно разные surname'ы → пустой list (no support, no contradicts)."""
    out = _RULE.apply({"surname": "Smith"}, {"surname": "Zhitnitzky"}, {})
    assert out == []


def test_missing_surname_a_no_evidence() -> None:
    out = _RULE.apply({}, {"surname": "Smith"}, {})
    assert out == []


def test_missing_surname_b_no_evidence() -> None:
    out = _RULE.apply({"surname": "Smith"}, {}, {})
    assert out == []


def test_empty_surname_no_evidence() -> None:
    out = _RULE.apply({"surname": ""}, {"surname": "Smith"}, {})
    assert out == []


def test_observation_contains_provenance() -> None:
    out = _RULE.apply({"surname": "Smith"}, {"surname": "Smith"}, {})
    assert out[0].source_provenance.get("algorithm") == "daitch_mokotoff"
    assert out[0].source_provenance.get("package") == "entity-resolution"


def test_observation_includes_bucket_codes() -> None:
    """Observation для UI должен содержать DM-codes (анонимные phonetic-keys)."""
    out = _RULE.apply({"surname": "Smith"}, {"surname": "Smith"}, {})
    assert "shared=" in out[0].observation
