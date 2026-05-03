"""Tests for Phase 26.12 Sephardic/Mizrahi crossover detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import sephardic_mizrahi_crossover
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_14_PATH = (
    REPO_ROOT
    / "data/test_corpus/trees/tree_14_sephardic_mizrahi_crossover_false_ashkenazi_merge.json"
)


def load_tree_14() -> dict[str, Any]:
    data = json.loads(TREE_14_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_14_emits_population_context_flags() -> None:
    output = run_tree(load_tree_14())

    assert "non_ashkenazi_jewish_crossover_false_ashkenazi_merge" in output["engine_flags"]
    assert "mountain_jewish_cluster_not_pale_ashkenazi" in output["engine_flags"]
    assert "broad_jewish_dna_overlap_not_branch_proof" in output["engine_flags"]
    assert "same_name_place_name_false_equivalence" in output["engine_flags"]
    assert "public_tree_population_context_collapse" in output["engine_flags"]
    assert "kaplan_kaplunov_false_equivalence" in output["engine_flags"]
    assert "population_context_required" in output["engine_flags"]


def test_tree_14_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_14())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_14_001"] is True
    assert evaluation_results["eval_14_002"] is True
    assert evaluation_results["eval_14_003"] is True
    assert evaluation_results["eval_14_004"] is True
    assert evaluation_results["eval_14_005"] is True


def test_tree_14_confirms_ashkenazi_branch() -> None:
    output = run_tree(load_tree_14())

    assert any(
        item.get("claim_type") == "confirmed_branch"
        and item.get("status") == "confirmed"
        and "Ashkenazi" in item.get("subject", "")
        for item in output["relationship_claims"]
    )


def test_tree_14_keeps_bukharian_and_mountain_clusters_separate() -> None:
    output = run_tree(load_tree_14())

    assert any(
        item.get("claim_type") == "separate_population_cluster"
        and "Bukharian" in item.get("subject", "")
        and item.get("status") == "kept_separate"
        for item in output["relationship_claims"]
    )
    assert any(
        item.get("claim_type") == "separate_population_cluster"
        and "Mountain Jewish" in item.get("subject", "")
        and item.get("status") == "kept_separate"
        for item in output["relationship_claims"]
    )


def test_tree_14_rejects_false_population_merges() -> None:
    output = run_tree(load_tree_14())

    assert any(
        item.get("merge_pair") == ["I5", "I7"]
        and item.get("status") == "Rejected"
        and item.get("action") == "do_not_merge"
        for item in output["merge_decisions"]
    )


def test_tree_14_quarantines_population_context_collapse() -> None:
    output = run_tree(load_tree_14())

    assert any(
        item.get("claim_type") == "public_tree_population_context_collapse"
        and item.get("status") == "quarantined"
        for item in output["quarantined_claims"]
    )


def test_detector_does_not_fire_without_population_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_population_context",
        "embedded_errors": [
            {
                "type": "population_context_error",
                "expected_flag": "non_ashkenazi_jewish_crossover_false_ashkenazi_merge",
            }
        ],
        "input_dna_matches": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = sephardic_mizrahi_crossover.detect(tree)

    assert result.engine_flags == []
    assert result.relationship_claims == []


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "population_context_required",
            "broad_jewish_dna_overlap_not_branch_proof",
        ],
        "embedded_errors": [],
        "input_dna_matches": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = sephardic_mizrahi_crossover.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_tree_14_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_14_sephardic_mizrahi_crossover_false_ashkenazi_merge",
            "--output",
            "reports/eval/test_phase_26_12_tree_14_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_14_sephardic_mizrahi_crossover_false_ashkenazi_merge" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_12_tree_14_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
