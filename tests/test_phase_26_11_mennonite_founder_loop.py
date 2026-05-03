"""Tests for Phase 26.11 Mennonite founder-loop detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import mennonite_founder_loop
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_13_PATH = (
    REPO_ROOT / "data/test_corpus/trees/tree_13_mennonite_colony_founder_loop_ambiguity.json"
)


def load_tree_13() -> dict[str, Any]:
    data = json.loads(TREE_13_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_13_emits_mennonite_founder_loop_flags() -> None:
    output = run_tree(load_tree_13())

    assert "fictional_bridge_person" in output["engine_flags"]
    assert "mennonite_jewish_or_slavic_boundary_error" in output["engine_flags"]
    assert "pedigree_collapse_mennonite_colony_founder_loop" in output["engine_flags"]
    assert "same_name_different_person_colony_context" in output["engine_flags"]
    assert "pedigree_collapse_endogamy_small_segment_overuse" in output["engine_flags"]
    assert "online_tree_fictional_bridge" in output["engine_flags"]
    assert "direct_pedigree_insertion_blocked" in output["engine_flags"]


def test_tree_13_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_13())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_13_001"] is True
    assert evaluation_results["eval_13_002"] is True
    assert evaluation_results["eval_13_003"] is True
    assert evaluation_results["eval_13_004"] is True
    assert evaluation_results["eval_13_005"] is True


def test_tree_13_confirms_batensky_dodatko_branch() -> None:
    output = run_tree(load_tree_13())

    assert any(
        item.get("claim_type") == "confirmed_branch"
        and item.get("subject") == "Batensky/Dodatko paternal branch"
        and item.get("status") == "confirmed"
        for item in output["relationship_claims"]
    )


def test_tree_13_classifies_mennonite_as_separate_probable_cluster() -> None:
    output = run_tree(load_tree_13())

    assert any(
        item.get("claim_type") == "probable_cluster"
        and item.get("status") == "separate_probable_cluster"
        for item in output["relationship_claims"]
    )


def test_tree_13_rejects_anna_friesen_merge() -> None:
    output = run_tree(load_tree_13())

    assert any(
        item.get("merge_pair") == ["I5", "I9"]
        and item.get("status") == "Rejected"
        and item.get("action") == "do_not_merge"
        for item in output["merge_decisions"]
    )


def test_tree_13_quarantines_fictional_bridge_claims() -> None:
    output = run_tree(load_tree_13())

    assert any(
        item.get("claim_type") == "fictional_bridge_person" and item.get("status") == "rejected"
        for item in output["quarantined_claims"]
    )
    assert any(
        item.get("claim_type") == "online_tree_fictional_bridge"
        and item.get("status") == "quarantined"
        for item in output["quarantined_claims"]
    )


def test_detector_does_not_fire_without_mennonite_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_mennonite_context",
        "embedded_errors": [
            {"type": "fictional_bridge", "expected_flag": "fictional_bridge_person"}
        ],
        "input_dna_matches": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = mennonite_founder_loop.detect(tree)

    assert result.engine_flags == []
    assert result.relationship_claims == []


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "fictional_bridge_person",
            "direct_pedigree_insertion_blocked",
        ],
        "embedded_errors": [],
        "input_dna_matches": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = mennonite_founder_loop.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_tree_13_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_13_mennonite_colony_founder_loop_ambiguity",
            "--output",
            "reports/eval/test_phase_26_11_tree_13_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_13_mennonite_colony_founder_loop_ambiguity" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_11_tree_13_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
