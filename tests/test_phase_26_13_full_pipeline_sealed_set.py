"""Tests for Phase 26.13 full-pipeline sealed-set detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import full_pipeline_sealed_set
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_20_PATH = (
    REPO_ROOT
    / "data/test_corpus/trees/tree_20_full_pipeline_sealed_set_contradiction_resolution.json"
)


def load_tree_20() -> dict[str, Any]:
    data = json.loads(TREE_20_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_20_emits_full_pipeline_flags() -> None:
    output = run_tree(load_tree_20())

    assert "dna_vs_tree_parentage_contradiction" in output["engine_flags"]
    assert "adoption_foster_guardian_as_parent" in output["engine_flags"]
    assert "fictional_bridge_person" in output["engine_flags"]
    assert "rabbinical_famous_line_bridge" in output["engine_flags"]
    assert "old_name_used_for_wrong_period" in output["engine_flags"]
    assert "modern_country_for_pre1917_record" in output["engine_flags"]
    assert "multi_path_relationship_required" in output["engine_flags"]
    assert "tiny_dna_match_used_for_medieval_descent" in output["engine_flags"]
    assert "compound_public_tree_contamination" in output["engine_flags"]
    assert "sealed_set_biological_parentage_candidate" in output["engine_flags"]
    assert "sealed_set_confirmed_branch_candidate" in output["engine_flags"]


def test_tree_20_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_20())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_20_001"] is True
    assert evaluation_results["eval_20_002"] is True
    assert evaluation_results["eval_20_003"] is True
    assert evaluation_results["eval_20_004"] is True
    assert evaluation_results["eval_20_005"] is True
    assert evaluation_results["eval_20_006"] is True
    assert evaluation_results["eval_20_007"] is True
    assert evaluation_results["eval_20_008"] is True


def test_tree_20_confirms_batensky_biological_parentage() -> None:
    output = run_tree(load_tree_20())

    assert any(
        item.get("claim_type") == "biological_parentage"
        and item.get("father_name") == "Alexander /Batensky/"
        and item.get("status") == "confirmed"
        for item in output["relationship_claims"]
    )


def test_tree_20_preserves_ivan_as_social_adoptive_only() -> None:
    output = run_tree(load_tree_20())

    assert any(
        item.get("claim_type") == "social_adoptive_parent"
        and item.get("father_name") == "Ivan /Danilov/"
        and item.get("status") == "confirmed_social_adoptive_not_biological"
        for item in output["relationship_claims"]
    )


def test_tree_20_confirms_maternal_branches() -> None:
    output = run_tree(load_tree_20())

    assert any(
        item.get("claim_type") == "confirmed_branch" and item.get("subject") == "Levitin branch"
        for item in output["relationship_claims"]
    )
    assert any(
        item.get("claim_type") == "confirmed_branch"
        and item.get("subject") == "Katz/Scherbatenko branch"
        for item in output["relationship_claims"]
    )


def test_tree_20_quarantines_bad_bridges_and_public_tree() -> None:
    output = run_tree(load_tree_20())

    assert any(
        item.get("claim_type") == "fictional_mennonite_bridge"
        for item in output["quarantined_claims"]
    )
    assert any(
        item.get("claim_type") == "famous_rabbinical_bridge"
        for item in output["quarantined_claims"]
    )
    assert any(
        item.get("claim_type") == "compound_public_tree_contamination"
        for item in output["quarantined_claims"]
    )


def test_tree_20_outputs_place_corrections_and_sealed_candidates() -> None:
    output = run_tree(load_tree_20())

    assert any(
        "Dnipropetrovsk" in item.get("accepted_value", "") for item in output["place_corrections"]
    )
    assert any(
        "Brest-Litovsk" in item.get("accepted_value", "") for item in output["place_corrections"]
    )
    assert any(
        item.get("candidate_type") == "biological_parentage"
        for item in output["sealed_set_candidates"]
    )
    assert any(
        item.get("candidate_type") == "confirmed_branch" for item in output["sealed_set_candidates"]
    )


def test_detector_does_not_fire_without_full_pipeline_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_full_pipeline_context",
        "embedded_errors": [
            {"type": "npe_conflict", "expected_flag": "dna_vs_tree_parentage_contradiction"}
        ],
        "input_dna_matches": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = full_pipeline_sealed_set.detect(tree)

    assert result.engine_flags == []
    assert result.relationship_claims == []


def test_tree_20_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_20_full_pipeline_sealed_set_contradiction_resolution",
            "--output",
            "reports/eval/test_phase_26_13_tree_20_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_20_full_pipeline_sealed_set_contradiction_resolution" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_13_tree_20_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
