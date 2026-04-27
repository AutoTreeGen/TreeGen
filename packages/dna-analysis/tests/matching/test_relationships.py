"""Тесты predict_relationship на основе Shared cM Project 4.0.

Никаких реальных DNA-данных. Тестируем только функцию-чёрный-ящик
от total_shared_cm к ranked relationship-кандидатам.
"""

from __future__ import annotations

import pytest
from dna_analysis import RelationshipRange, predict_relationship


def test_zero_cm_returns_unrelated() -> None:
    result = predict_relationship(0.0)
    assert len(result) == 1
    assert result[0].label.startswith("Unrelated")
    assert result[0].probability == pytest.approx(1.0)
    assert result[0].cm_range == (0, 0)


def test_below_noise_threshold_returns_unrelated() -> None:
    """< 7 cM (noise floor из ADR-0014) → unrelated, не distant cousin."""
    result = predict_relationship(5.0)
    assert result[0].label.startswith("Unrelated")


def test_3500_cm_returns_identical_twin_top_match() -> None:
    """3500 cM попадает в identical-twin диапазон (3400-4000)."""
    result = predict_relationship(3500.0)
    assert result[0].label.startswith("Identical twin")
    assert result[0].cm_range == (3400, 4000)
    # Parent/Child тоже частично перекрывает (2376-3720) → может быть в списке.
    labels = [r.label for r in result]
    assert any("Parent" in lbl for lbl in labels)


def test_3800_cm_above_all_ranges_returns_identical_twin() -> None:
    """Очень большой cM выше всех диапазонов → fallback на identical twin."""
    result = predict_relationship(5000.0)
    assert len(result) == 1
    assert result[0].label.startswith("Identical twin")


def test_2700_cm_returns_full_sibling_top() -> None:
    """2700 cM — middle of full-sibling range (1613-3488, mean 2613)."""
    result = predict_relationship(2700.0)
    assert result[0].label.startswith("Full sibling")


def test_1700_cm_returns_grandparent_aunt_uncle_group_top() -> None:
    """1700 cM — около mean 1759 grandparent-group (1156-2311)."""
    result = predict_relationship(1700.0)
    # Топ должен быть grandparent group; full sibling (1613-3488) тоже candidate.
    assert "Grandparent" in result[0].label or "Full sibling" in result[0].label


def test_800_cm_returns_first_cousin_in_top_three() -> None:
    """800 cM около mean 866 для 1st-cousin / great-grandparent group."""
    result = predict_relationship(800.0)
    top_three = [r.label for r in result[:3]]
    assert any("1st cousin" in lbl for lbl in top_three) or any(
        "Great-aunt" in lbl or "Great-grandparent" in lbl for lbl in top_three
    )


def test_400_cm_overlap_returns_multiple_candidates() -> None:
    """400 cM попадает в несколько диапазонов (1C, 1C1R, 2C может быть out)."""
    result = predict_relationship(400.0)
    assert len(result) >= 2
    # Probabilities должны быть нормализованы (сумма = 1.0).
    assert sum(r.probability for r in result) == pytest.approx(1.0)


def test_75_cm_returns_distant_cousin_candidates() -> None:
    """75 cM — 3C-4C territory (overlapping ranges)."""
    result = predict_relationship(75.0)
    labels = [r.label for r in result]
    assert any("2nd cousin" in lbl or "3rd cousin" in lbl or "4th cousin" in lbl for lbl in labels)


def test_probabilities_normalize_to_one() -> None:
    """Sum of probabilities в результате = 1.0 ±epsilon."""
    for total_cm in (50.0, 100.0, 300.0, 800.0, 1500.0, 2500.0):
        result = predict_relationship(total_cm)
        assert sum(r.probability for r in result) == pytest.approx(1.0), (
            f"normalization broken for {total_cm} cM"
        )


def test_results_sorted_by_probability_descending() -> None:
    result = predict_relationship(800.0)
    probs = [r.probability for r in result]
    assert probs == sorted(probs, reverse=True), "result not sorted descending by probability"


def test_relationship_range_carries_attribution() -> None:
    """Source attribution в каждом RelationshipRange (CC-BY 4.0)."""
    result = predict_relationship(800.0)
    for r in result:
        assert "Shared cM Project" in r.source
        assert "CC-BY" in r.source
        assert "Bettinger" in r.source


def test_relationship_range_is_frozen() -> None:
    rr = RelationshipRange(label="test", probability=0.5, cm_range=(0, 100))
    with pytest.raises(Exception, match=r"frozen|validation"):
        rr.probability = 0.7  # type: ignore[misc]


def test_negative_cm_raises() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        predict_relationship(-1.0)


def test_longest_segment_argument_is_accepted_but_unused_in_phase_6_1() -> None:
    """longest_segment_cm передаётся, но не влияет на результат в Phase 6.1.

    Phase 6.4 — phasing / IBD2 будет использовать этот сигнал для
    разрешения parent vs full-sibling.
    """
    a = predict_relationship(800.0, longest_segment_cm=50.0)
    b = predict_relationship(800.0, longest_segment_cm=150.0)
    assert [r.label for r in a] == [r.label for r in b]
    assert [r.probability for r in a] == [r.probability for r in b]
