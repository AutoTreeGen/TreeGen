"""Tests for Phase 26.10 Ashkenazi endogamy multi-path detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import ashkenazi_endogamy
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_12_PATH = (
    REPO_ROOT / "data/test_corpus/trees/tree_12_ashkenazi_endogamy_multi_path_relationship.json"
)


def load_tree_12() -> dict[str, Any]:
    data = json.loads(TREE_12_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_12_emits_endogamy_flags() -> None:
    output = run_tree(load_tree_12())

    assert "pedigree_collapse_ashkenazi_single_path_error" in output["engine_flags"]
    assert "pedigree_collapse_endogamy_small_segment_overuse" in output["engine_flags"]
    assert "public_tree_single_path_overcompression" in output["engine_flags"]
    assert "multi_path_relationship_required" in output["engine_flags"]
    assert "katz_feldman_cluster_not_noise" in output["engine_flags"]
    assert "shared_match_cluster_split" in output["engine_flags"]
    assert "triangulated_segments_support_distinct_paths" in output["engine_flags"]


def test_tree_12_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_12())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_12_001"] is True
    assert evaluation_results["eval_12_002"] is True
    assert evaluation_results["eval_12_003"] is True
    assert evaluation_results["eval_12_004"] is True
    assert evaluation_results["eval_12_005"] is True


def test_tree_12_outputs_two_probable_paths() -> None:
    output = run_tree(load_tree_12())

    assert any(
        item.get("claim_type") == "probable_relationship_path"
        and item.get("path_name") == "Levitin-Friedman"
        and item.get("status") == "probable"
        for item in output["relationship_claims"]
    )
    assert any(
        item.get("claim_type") == "probable_relationship_path"
        and item.get("path_name") == "Katz-Feldman"
        and item.get("status") == "probable"
        for item in output["relationship_claims"]
    )


def test_tree_12_requires_multi_path_model() -> None:
    output = run_tree(load_tree_12())

    assert any(
        item.get("claim_type") == "multi_path_model"
        and item.get("status") == "required"
        and item.get("paths") == ["Levitin-Friedman", "Katz-Feldman"]
        for item in output["relationship_claims"]
    )


def test_tree_12_quarantines_single_path_and_small_segment_anchor() -> None:
    output = run_tree(load_tree_12())

    assert any(
        item.get("claim_type") == "single_path_relationship_assignment"
        and item.get("status") == "rejected"
        for item in output["quarantined_claims"]
    )
    assert any(
        item.get("claim_type") == "small_segment_proof_anchor"
        and item.get("status") == "rejected"
        and item.get("match_id") == "dna_1206"
        for item in output["quarantined_claims"]
    )


def test_detector_does_not_fire_without_endogamy_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_endogamy_context",
        "embedded_errors": [
            {
                "type": "endogamy_error",
                "expected_flag": "pedigree_collapse_ashkenazi_single_path_error",
            }
        ],
        "input_dna_matches": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = ashkenazi_endogamy.detect(tree)

    assert result.engine_flags == []
    assert result.relationship_claims == []


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "multi_path_relationship_required",
            "triangulated_segments_support_distinct_paths",
        ],
        "embedded_errors": [],
        "input_dna_matches": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = ashkenazi_endogamy.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_tree_12_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_12_ashkenazi_endogamy_multi_path_relationship",
            "--output",
            "reports/eval/test_phase_26_10_tree_12_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_12_ashkenazi_endogamy_multi_path_relationship" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_10_tree_12_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
