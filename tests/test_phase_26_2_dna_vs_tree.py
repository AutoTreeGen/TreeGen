"""Phase 26.2 — DNA-vs-tree contradiction detector tests.

Покрывает (ADR-0085):

- Tree 11 эмитит ``dna_vs_tree_parentage_contradiction``.
- Tree 11 эмитит ``relationship_claims``: bio confirmed, social confirmed,
  bio rejected.
- Tree 11 эмитит хотя бы один ``sealed_set_candidate``.
- Tree 11 score > 0.10 baseline.
- Trees без strong DNA evidence — детектор молчит.
- Detector — pure: НЕ читает ``expected_engine_flags`` /
  ``expected_confidence_outputs``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from inference_engine.detectors.dna_vs_tree import (
    CLOSE_RELATIVE_CM_THRESHOLD,
    detect,
)
from inference_engine.detectors.registry import DetectorResult
from inference_engine.engine import run_tree

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TREES_DIR = _REPO_ROOT / "data" / "test_corpus" / "trees"
_HARNESS_FILE = (
    _REPO_ROOT
    / "data"
    / "test_corpus"
    / "harness"
    / "autotreegen_evaluation_harness_trees1_20.json"
)


def _load(name: str) -> dict[str, Any]:
    return json.loads((_TREES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tree_11() -> dict[str, Any]:
    return _load("tree_11_unknown_father_npe_dna_contradiction.json")


@pytest.fixture(scope="module")
def tree_11_output(tree_11: dict[str, Any]) -> dict[str, Any]:
    return run_tree(tree_11)


# ---------------------------------------------------------------------------
# Engine flags
# ---------------------------------------------------------------------------


def test_tree_11_emits_dna_vs_tree_parentage_contradiction(
    tree_11_output: dict[str, Any],
) -> None:
    flags = set(tree_11_output["engine_flags"])
    assert "dna_vs_tree_parentage_contradiction" in flags


def test_tree_11_emits_adoption_foster_guardian_as_parent(
    tree_11_output: dict[str, Any],
) -> None:
    assert "adoption_foster_guardian_as_parent" in set(tree_11_output["engine_flags"])


def test_tree_11_emits_sealed_set_biological_parentage_candidate_flag(
    tree_11_output: dict[str, Any],
) -> None:
    assert "sealed_set_biological_parentage_candidate" in set(tree_11_output["engine_flags"])


# ---------------------------------------------------------------------------
# Relationship claims
# ---------------------------------------------------------------------------


def test_tree_11_emits_three_relationship_claims(
    tree_11_output: dict[str, Any],
) -> None:
    claims = tree_11_output["relationship_claims"]
    assert len(claims) >= 3, f"expected ≥3 relationship_claims, got {len(claims)}"


def test_tree_11_confirmed_biological_father_is_i4(
    tree_11_output: dict[str, Any],
) -> None:
    """Bio-кандидат должен быть I4 (Alexander Batensky) — он назван в
    user_assertion с DNA evidence.
    """
    confirmed_bio = [
        c
        for c in tree_11_output["relationship_claims"]
        if c.get("relationship_role") == "biological_father" and c.get("status") == "Confirmed"
    ]
    assert confirmed_bio, "expected one Confirmed biological_father claim"
    assert any(c.get("person_id") == "I4" for c in confirmed_bio)


def test_tree_11_rejected_biological_father_is_i3(
    tree_11_output: dict[str, Any],
) -> None:
    rejected_bio = [
        c
        for c in tree_11_output["relationship_claims"]
        if c.get("relationship_role") == "biological_father" and c.get("status") == "Rejected"
    ]
    assert rejected_bio, "expected one Rejected biological_father claim"
    assert any(c.get("person_id") == "I3" for c in rejected_bio)


def test_tree_11_confirmed_social_father_is_i3(
    tree_11_output: dict[str, Any],
) -> None:
    social = [
        c
        for c in tree_11_output["relationship_claims"]
        if c.get("relationship_role") == "social_or_adoptive_father"
        and c.get("status") == "Confirmed"
    ]
    assert social, "expected one Confirmed social_or_adoptive_father claim"
    assert any(c.get("person_id") == "I3" for c in social)


# ---------------------------------------------------------------------------
# Sealed set candidates
# ---------------------------------------------------------------------------


def test_tree_11_emits_at_least_one_sealed_set_candidate(
    tree_11_output: dict[str, Any],
) -> None:
    candidates = tree_11_output["sealed_set_candidates"]
    assert len(candidates) >= 1
    assert any(
        c.get("claim_type") == "biological_parentage" and c.get("subject_person_id") == "I4"
        for c in candidates
    )


# ---------------------------------------------------------------------------
# Score above baseline
# ---------------------------------------------------------------------------


def test_tree_11_score_increases_above_baseline(tree_11: dict[str, Any]) -> None:
    """Phase 26.1 baseline для tree_11 = 0.7*0 + 0.2*0 + 0.1*1 = 0.10.

    После 26.2 детектора score должен подняться: ≥3 expected flags
    matched, ≥3 assertions поддержаны.
    """
    out = run_tree(tree_11)

    expected_flags = set(tree_11["expected_engine_flags"])
    actual_flags = set(out["engine_flags"])
    flag_score = len(expected_flags & actual_flags) / len(expected_flags) if expected_flags else 1.0

    assertion_ids = [a["assertion_id"] for a in tree_11["evaluation_assertions"]]
    eval_results = out["evaluation_results"]
    passed = sum(1 for aid in assertion_ids if eval_results.get(aid) is True)
    assertion_score = passed / len(assertion_ids) if assertion_ids else 1.0

    score = 0.7 * assertion_score + 0.2 * flag_score + 0.1 * 1.0
    assert score > 0.10, (
        f"tree_11 score {score:.4f} should exceed baseline 0.10 "
        f"(assertion_score={assertion_score:.3f}, flag_score={flag_score:.3f})"
    )
    # Sanity: реально что-то прошло.
    assert passed >= 3, f"expected ≥3 assertions passed, got {passed}"


# ---------------------------------------------------------------------------
# Negative cases — detector remains silent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tree_filename",
    [
        # Patronymic tree — max shared_cm = 118 (cousin-level).
        "tree_07_patronymic_vs_surname_disambiguation.json",
        # Endogamy multi-path — max shared_cm = 116.
        "tree_12_ashkenazi_endogamy_multi_path_relationship.json",
        # Holocaust gap — max shared_cm = 86.
        "tree_05_brest_litovsk_holocaust_gap_reconstruction.json",
        # Rabbinical hypothesis — max shared_cm = 68.
        "tree_06_rabbi_kamenetsky_hypothesis_not_confirmed.json",
    ],
)
def test_trees_without_strong_dna_do_not_emit_contradiction(tree_filename: str) -> None:
    """Trees, где max shared_cm < 1300 (cluster-level или ниже), должны
    оставаться без contradiction-флага."""
    tree = _load(tree_filename)
    out = run_tree(tree)
    flags = set(out["engine_flags"])
    assert "dna_vs_tree_parentage_contradiction" not in flags, (
        f"detector should not fire on {tree_filename}: max shared_cm < threshold"
    )
    assert "adoption_foster_guardian_as_parent" not in flags
    assert "sealed_set_biological_parentage_candidate" not in flags


def test_detector_silent_when_dna_strong_but_no_social_context() -> None:
    """Strong DNA + DNA-supported bio claim, но БЕЗ social/adoptive контекста
    → детектор молчит (нет contradiction)."""
    tree = _load("tree_11_unknown_father_npe_dna_contradiction.json")
    tree = copy.deepcopy(tree)
    # Удаляем все social/adoptive сигналы.
    tree["input_user_assertions"] = [
        a
        for a in tree["input_user_assertions"]
        if "social" not in (a.get("evidence") or "").lower()
        and "surname" not in (a.get("evidence") or "").lower()
    ]
    tree["input_archive_snippets"] = [
        s for s in tree["input_archive_snippets"] if s.get("type") != "adoption_or_name_change"
    ]
    # И прибираем social-related NOTE из GEDCOM excerpt.
    tree["input_gedcom_excerpt"] = "\n".join(
        line
        for line in tree["input_gedcom_excerpt"].splitlines()
        if "social/adoptive father" not in line.lower()
    )
    out = run_tree(tree)
    assert "dna_vs_tree_parentage_contradiction" not in out["engine_flags"]


def test_detector_silent_when_social_context_but_no_strong_dna() -> None:
    """Social context присутствует, но DNA слабая → детектор молчит."""
    tree = _load("tree_11_unknown_father_npe_dna_contradiction.json")
    tree = copy.deepcopy(tree)
    # Понижаем все cM ниже cluster-level (< 100 cM).
    for m in tree["input_dna_matches"]:
        m["shared_cm"] = 50
    out = run_tree(tree)
    assert "dna_vs_tree_parentage_contradiction" not in out["engine_flags"]


# ---------------------------------------------------------------------------
# Anti-cheat
# ---------------------------------------------------------------------------


def test_detector_does_not_read_expected_engine_flags(tree_11: dict[str, Any]) -> None:
    """Сменив ``expected_engine_flags`` на garbage, мы не должны менять
    output — детектор не имеет права их читать.
    """
    out_clean = run_tree(tree_11)
    poisoned = copy.deepcopy(tree_11)
    poisoned["expected_engine_flags"] = ["completely_made_up_flag", "another_fake"]
    out_poisoned = run_tree(poisoned)
    assert out_clean["engine_flags"] == out_poisoned["engine_flags"]
    assert out_clean["evaluation_results"] == out_poisoned["evaluation_results"]


def test_detector_does_not_read_expected_confidence_outputs(
    tree_11: dict[str, Any],
) -> None:
    """Дёргая ``expected_confidence_outputs``, output не меняется."""
    out_clean = run_tree(tree_11)
    poisoned = copy.deepcopy(tree_11)
    poisoned["expected_confidence_outputs"] = {"garbage": "value"}
    out_poisoned = run_tree(poisoned)
    assert out_clean == out_poisoned


def test_detector_does_not_special_case_tree_id(tree_11: dict[str, Any]) -> None:
    """Сменив tree_id на произвольную строку, детектор всё равно срабатывает —
    он смотрит на DNA/assertion evidence, а не на id."""
    poisoned = copy.deepcopy(tree_11)
    poisoned["tree_id"] = "tree_99_arbitrary"
    # evaluation_assertions содержит assertion_id вида eval_11_NNN — не трогаем,
    # потому что детектор matchит по структуре expected, не по prefix.
    out = run_tree(poisoned)
    assert "dna_vs_tree_parentage_contradiction" in set(out["engine_flags"])


def test_detector_emits_nothing_on_minimal_tree() -> None:
    """На tree без DNA/assertion-evidence детектор возвращает пустой
    DetectorResult."""
    minimal: dict[str, Any] = {"tree_id": "tree_00_smoke"}
    res = detect(minimal)
    assert isinstance(res, DetectorResult)
    assert res.engine_flags == []
    assert res.relationship_claims == []
    assert res.sealed_set_candidates == []
    assert res.evaluation_results == {}


# ---------------------------------------------------------------------------
# Threshold sanity
# ---------------------------------------------------------------------------


def test_close_relative_threshold_is_paternal_relevant() -> None:
    """1300 cM — нижняя граница half-sibling/parent/aunt-uncle range.
    Ловим случайные регрессии в численном пороге."""
    assert 1100 <= CLOSE_RELATIVE_CM_THRESHOLD <= 1500
