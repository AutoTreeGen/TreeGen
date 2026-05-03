"""Phase 26.3 — GEDCOM safe-merge / multi-source conflict detector tests.

Покрывает (ADR-0086):

- Tree 15 эмитит safe-merge engine_flags (different_export_ids,
  alias_identity, disconnected_profile, source_media_loss,
  relationship_type_annotation, rollback_audit_required).
- Tree 15 эмитит merge_decisions, по одному на каждую найденную пару;
  canonical_name берётся из более информативной формы; aliases
  preserved.
- Tree 15 marks eval_15_001 / 002 / 005 / 006 как True.
- Tree 15 score поднимается выше Phase 26.2 baseline (≈0.36).
- Single-source trees (≤ 1 HEAD) — детектор молчит.
- Anti-cheat: детектор НЕ читает ``expected_engine_flags``,
  ``expected_confidence_outputs``, ``embedded_errors[].expected_flag``,
  ``ground_truth_annotations`` или сам ``tree_id``.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from inference_engine.detectors import gedcom_safe_merge
from inference_engine.detectors.gedcom_safe_merge import (
    MIN_NAME_SIMILARITY,
    PAIR_SCORE_THRESHOLD,
)
from inference_engine.detectors.result import DetectorResult
from inference_engine.engine import run_tree

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TREES_DIR = _REPO_ROOT / "data" / "test_corpus" / "trees"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_TREES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tree_15() -> dict[str, Any]:
    return _load("tree_15_gedcom_safe_merge_conflicting_sources.json")


@pytest.fixture(scope="module")
def tree_15_output(tree_15: dict[str, Any]) -> dict[str, Any]:
    return run_tree(tree_15)


# ---------------------------------------------------------------------------
# Engine flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    [
        "same_person_different_export_ids",
        "same_person_alias_identity",
        "same_person_disconnected_profile",
        "adoptive_as_biological_parent_in_import",
        "gedcom_export_source_media_loss",
        "safe_merge_requires_relationship_type_annotation",
        "rollback_audit_required",
    ],
)
def test_tree_15_emits_safe_merge_flag(tree_15_output: dict[str, Any], flag: str) -> None:
    assert flag in set(tree_15_output["engine_flags"]), (
        f"expected {flag!r} in engine_flags, got {tree_15_output['engine_flags']}"
    )


# ---------------------------------------------------------------------------
# Merge decisions
# ---------------------------------------------------------------------------


def test_tree_15_emits_daniel_merge(tree_15_output: dict[str, Any]) -> None:
    """A_I1 (Daniel Zalman Zhitnitsky) ↔ B_I100 (Daniel Z. Zhitnitzky)."""
    decisions = tree_15_output["merge_decisions"]
    assert decisions, "expected ≥1 merge_decision"
    daniel = _find_decision(decisions, ["A_I1", "B_I100"])
    assert daniel is not None
    assert daniel["status"] == "Confirmed"
    assert daniel["action"] == "merge"
    assert daniel["is_alias"] is False
    assert daniel["preserve_sources"] is True
    assert "Daniel Zalman" in daniel["canonical_name"]


def test_tree_15_emits_vlad_alias_merge(tree_15_output: dict[str, Any]) -> None:
    """A_I2 (Vlad Aaron Zhitnitzky) ↔ B_I200 (Vladimir Ivanovich Danilov) — alias."""
    decisions = tree_15_output["merge_decisions"]
    vlad = _find_decision(decisions, ["A_I2", "B_I200"])
    assert vlad is not None
    assert vlad["is_alias"] is True
    assert vlad["aliases_preserved"] is True
    assert vlad["aliases"], "alias-merge must preserve at least one alias"
    assert vlad["action"] == "merge_with_aliases"


def test_tree_15_emits_alexander_disconnected_merge(tree_15_output: dict[str, Any]) -> None:
    """A_I5 (in F2) ↔ B_I600 (disconnected) — must reconnect."""
    decisions = tree_15_output["merge_decisions"]
    alexander = _find_decision(decisions, ["A_I5", "B_I600"])
    assert alexander is not None
    assert alexander["is_disconnected"] is True
    assert alexander["action"] == "merge_and_reconnect"


def test_tree_15_merges_olga_and_tatyana(tree_15_output: dict[str, Any]) -> None:
    """Family-role propagation should pair Olga (A_I3↔B_I300) and Tatyana (A_I4↔B_I400)."""
    decisions = tree_15_output["merge_decisions"]
    assert _find_decision(decisions, ["A_I3", "B_I300"]) is not None
    assert _find_decision(decisions, ["A_I4", "B_I400"]) is not None


def test_tree_15_merge_count_is_five(tree_15_output: dict[str, Any]) -> None:
    """Все 5 ground-truth merge-пар найдены: Daniel, Vlad, Olga, Tatyana, Alexander."""
    decisions = tree_15_output["merge_decisions"]
    assert len(decisions) == 5, (
        f"expected 5 merge pairs, got {len(decisions)}: {[d['merge_pair'] for d in decisions]}"
    )


# ---------------------------------------------------------------------------
# Evaluation assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "assertion_id",
    ["eval_15_001", "eval_15_002", "eval_15_005", "eval_15_006"],
)
def test_tree_15_marks_assertion_true(tree_15_output: dict[str, Any], assertion_id: str) -> None:
    assert tree_15_output["evaluation_results"][assertion_id] is True


# ---------------------------------------------------------------------------
# Score climb
# ---------------------------------------------------------------------------


def test_tree_15_score_climbs_above_phase_26_2_baseline(tmp_path: Path) -> None:
    """Phase 26.2-only baseline для tree_15 ≈ 0.36 (assert=2/6, flag=1/8).
    После 26.3 — assert=6/6 + flag=8/8 → score ≈ 1.0.
    """
    out = tmp_path / "tree_15_report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_15_gedcom_safe_merge_conflicting_sources",
            "--output",
            str(out),
        ],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "tree_15_gedcom_safe_merge_conflicting_sources" in completed.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]
    assert score >= 0.90, f"tree_15 score {score:.4f} should be ≥ 0.90 with both detectors"


# ---------------------------------------------------------------------------
# Negative cases — single-source trees stay silent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tree_filename",
    [
        "tree_07_patronymic_vs_surname_disambiguation.json",
        "tree_11_unknown_father_npe_dna_contradiction.json",
        "tree_12_ashkenazi_endogamy_multi_path_relationship.json",
        "tree_20_full_pipeline_sealed_set_contradiction_resolution.json",
    ],
)
def test_single_source_trees_do_not_trigger_safe_merge(tree_filename: str) -> None:
    """Только tree_15 имеет 2 HEAD-секции; на остальных детектор молчит."""
    tree = _load(tree_filename)
    result = gedcom_safe_merge.detect(tree)
    assert result.engine_flags == [], (
        f"single-source tree {tree_filename} should not emit safe-merge flags, "
        f"got {result.engine_flags}"
    )
    assert result.merge_decisions == []
    assert result.evaluation_results == {}


def test_minimal_tree_returns_empty_result() -> None:
    minimal: dict[str, Any] = {"tree_id": "tree_synthetic_minimal"}
    result = gedcom_safe_merge.detect(minimal)
    assert isinstance(result, DetectorResult)
    assert result.engine_flags == []
    assert result.merge_decisions == []
    assert result.evaluation_results == {}


def test_two_heads_but_no_cross_source_duplicates_stays_silent() -> None:
    """Две HEAD-секции, но люди разные → детектор не эмитит merge_decisions."""
    tree = {
        "tree_id": "tree_synthetic_two_heads_no_dup",
        "input_gedcom_excerpt": (
            "0 HEAD\n1 SOUR TreeA\n"
            "0 @A_I1@ INDI\n1 NAME Alice /Smith/\n1 BIRT\n2 DATE 1900\n"
            "0 TRLR\n"
            "0 HEAD\n1 SOUR TreeB\n"
            "0 @B_I1@ INDI\n1 NAME Zaharia /Petrov/\n1 BIRT\n2 DATE 1850\n"
            "0 TRLR\n"
        ),
    }
    result = gedcom_safe_merge.detect(tree)
    assert result.engine_flags == []
    assert result.merge_decisions == []


# ---------------------------------------------------------------------------
# Anti-cheat
# ---------------------------------------------------------------------------


def test_detector_does_not_read_expected_engine_flags(tree_15: dict[str, Any]) -> None:
    out_clean = run_tree(tree_15)
    poisoned = copy.deepcopy(tree_15)
    poisoned["expected_engine_flags"] = ["completely_made_up_flag"]
    out_poisoned = run_tree(poisoned)
    assert out_clean["engine_flags"] == out_poisoned["engine_flags"]
    assert out_clean["merge_decisions"] == out_poisoned["merge_decisions"]


def test_detector_does_not_read_embedded_errors_expected_flag(
    tree_15: dict[str, Any],
) -> None:
    """``embedded_errors[].expected_flag`` — это тот же answer key, что и
    ``expected_engine_flags``. Детектор обязан игнорировать эти поля."""
    out_clean = run_tree(tree_15)
    poisoned = copy.deepcopy(tree_15)
    for err in poisoned.get("embedded_errors") or []:
        err["expected_flag"] = "garbage_flag"
        err["reason"] = "garbage_reason"
        err["type"] = "garbage_type"
    out_poisoned = run_tree(poisoned)
    assert out_clean["engine_flags"] == out_poisoned["engine_flags"]
    assert out_clean["merge_decisions"] == out_poisoned["merge_decisions"]


def test_detector_does_not_read_expected_confidence_outputs(
    tree_15: dict[str, Any],
) -> None:
    out_clean = run_tree(tree_15)
    poisoned = copy.deepcopy(tree_15)
    poisoned["expected_confidence_outputs"] = {"garbage": "value"}
    poisoned["ground_truth_annotations"] = {"garbage": "value"}
    out_poisoned = run_tree(poisoned)
    assert out_clean == out_poisoned


def test_detector_does_not_special_case_tree_id(tree_15: dict[str, Any]) -> None:
    """Сменив tree_id на произвольную строку, детектор всё равно срабатывает."""
    poisoned = copy.deepcopy(tree_15)
    poisoned["tree_id"] = "tree_99_arbitrary_label"
    out = run_tree(poisoned)
    assert "same_person_different_export_ids" in set(out["engine_flags"])
    assert any(d["merge_pair"] == ["A_I1", "B_I100"] for d in out["merge_decisions"])


def test_detector_does_not_fire_when_only_answer_key_present() -> None:
    """Tree без gedcom_excerpt, но с заполненным answer-key — детектор молчит.
    Значит детектор НЕ извлекает решения из expected_* / embedded_errors полей.
    """
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "same_person_different_export_ids",
            "rollback_audit_required",
        ],
        "expected_confidence_outputs": {"merge_X_Y": {"status": "Confirmed"}},
        "ground_truth_annotations": {"true_merges": [["X", "Y"]]},
        "embedded_errors": [
            {"type": "gedcom_duplicate", "persons": ["X", "Y"], "expected_flag": "X"},
        ],
    }
    result = gedcom_safe_merge.detect(tree)
    assert result.engine_flags == []
    assert result.merge_decisions == []


# ---------------------------------------------------------------------------
# Threshold sanity
# ---------------------------------------------------------------------------


def test_pair_score_threshold_is_meaningful() -> None:
    """Anti-regression: threshold не должен быть 0 или 1 (всё попадало бы в merge)."""
    assert PAIR_SCORE_THRESHOLD >= 2


def test_min_name_similarity_in_reasonable_range() -> None:
    assert 0.5 <= MIN_NAME_SIMILARITY <= 0.95


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_decision(decisions: list[dict[str, Any]], pair: list[str]) -> dict[str, Any] | None:
    target = frozenset(pair)
    for d in decisions:
        if frozenset(d.get("merge_pair") or []) == target:
            return d
    return None
