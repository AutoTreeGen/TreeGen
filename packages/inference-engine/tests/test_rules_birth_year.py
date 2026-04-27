"""Тесты BirthYearMatchRule (Phase 7.1, помещён рядом с другими rule's).

Этот rule изначально упоминался в Phase 7.0 docstring как demo, но
реально зашиплен в Phase 7.1 (вместе с SurnameMatchRule и др.)
"""

from __future__ import annotations

from inference_engine.rules.birth_year import BirthYearMatchRule
from inference_engine.types import EvidenceDirection

_RULE = BirthYearMatchRule()


def test_rule_id_is_stable() -> None:
    assert _RULE.rule_id == "birth_year_match"


def test_exact_match_strong_supports() -> None:
    out = _RULE.apply({"birth_year": 1945}, {"birth_year": 1945}, {})
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS
    assert out[0].weight == 0.4


def test_close_within_two_years_supports() -> None:
    """±1, ±2 года — типичные ошибки переписи / GEDCOM-конверсии."""
    out = _RULE.apply({"birth_year": 1945}, {"birth_year": 1946}, {})
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.SUPPORTS
    assert out[0].weight == 0.25
    out = _RULE.apply({"birth_year": 1945}, {"birth_year": 1943}, {})
    assert len(out) == 1
    assert out[0].weight == 0.25


def test_far_difference_in_same_person_contradicts() -> None:
    """|Δ| ≥ 10 лет для same_person — соlid сигнал «не один человек»."""
    out = _RULE.apply(
        {"birth_year": 1945},
        {"birth_year": 1900},
        {"hypothesis_type": "same_person"},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.CONTRADICTS
    assert out[0].weight == 0.30


def test_far_difference_in_parent_child_no_contradicts() -> None:
    """Для parent_child разрыв 15-40 лет — норма."""
    out = _RULE.apply(
        {"birth_year": 1900},
        {"birth_year": 1925},
        {"hypothesis_type": "parent_child"},
    )
    # 25 лет ≥ 10 → но мы не CONTRADICT'им parent_child.
    for ev in out:
        assert ev.direction is not EvidenceDirection.CONTRADICTS


def test_grey_zone_no_evidence() -> None:
    """3 ≤ Δ < 10 — серая зона, никакого evidence."""
    out = _RULE.apply({"birth_year": 1945}, {"birth_year": 1950}, {})
    assert out == []


def test_missing_year_no_evidence() -> None:
    out = _RULE.apply({}, {"birth_year": 1945}, {})
    assert out == []
    out = _RULE.apply({"birth_year": 1945}, {}, {})
    assert out == []


def test_invalid_year_no_evidence() -> None:
    """Не-int значения (строки, None, плохой формат) → silent."""
    out = _RULE.apply({"birth_year": "not-a-year"}, {"birth_year": 1945}, {})
    assert out == []
