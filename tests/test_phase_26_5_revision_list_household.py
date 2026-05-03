"""Tests for Phase 26.5 revision-list household detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import revision_list_household
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_17_PATH = (
    REPO_ROOT / "data/test_corpus/trees/tree_17_revision_list_household_interpretation.json"
)


def load_tree_17() -> dict[str, Any]:
    data = json.loads(TREE_17_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_17_emits_revision_list_flags() -> None:
    output = run_tree(load_tree_17())

    assert "revision_list_missing_female_not_disproof" in output["engine_flags"]
    assert "same_name_same_guberniya_different_household" in output["engine_flags"]
    assert "unknown_wife_invented_from_missing_female_revision" in output["engine_flags"]
    assert "revision_list_age_drift_not_identity_conflict" in output["engine_flags"]
    assert "registered_vs_actual_residence_confusion" in output["engine_flags"]
    assert "raskes_raskin_variant_not_enough" in output["engine_flags"]
    assert "public_tree_revision_list_overreach" in output["engine_flags"]


def test_tree_17_marks_all_revision_assertions_true() -> None:
    output = run_tree(load_tree_17())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_17_001"] is True
    assert evaluation_results["eval_17_002"] is True
    assert evaluation_results["eval_17_003"] is True
    assert evaluation_results["eval_17_004"] is True
    assert evaluation_results["eval_17_005"] is True


def test_tree_17_confirms_sura_friedman_mother() -> None:
    output = run_tree(load_tree_17())

    assert any(
        item.get("claim_type") == "mother"
        and item.get("object_name") == "Sura /Friedman/"
        and item.get("status") == "confirmed"
        for item in output["relationship_claims"]
    )


def test_tree_17_rejects_wrong_merges() -> None:
    output = run_tree(load_tree_17())

    assert any(
        item.get("merge_pair") == ["I3", "I7"]
        and item.get("status") == "Rejected"
        and item.get("action") == "do_not_merge"
        for item in output["merge_decisions"]
    )
    assert any(
        item.get("merge_pair") == ["I5", "I8"]
        and item.get("status") == "Rejected"
        and item.get("action") == "keep_as_hypothesis_conflict"
        for item in output["merge_decisions"]
    )


def test_tree_17_quarantines_fabricated_revision_claims() -> None:
    output = run_tree(load_tree_17())

    assert any(item.get("claim_type") == "invented_spouse" for item in output["quarantined_claims"])
    assert any(
        item.get("claim_type") == "surname_variant_merge" for item in output["quarantined_claims"]
    )
    assert any(
        item.get("claim_type") == "public_tree_revision_list_claim"
        for item in output["quarantined_claims"]
    )


def test_detector_does_not_fire_without_revision_list_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_revision_list",
        "embedded_errors": [
            {
                "type": "same_name_different_person",
                "persons": ["A", "B"],
                "expected_flag": "same_name_same_guberniya_different_household",
            }
        ],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = revision_list_household.detect(tree)

    assert result.engine_flags == []
    assert result.merge_decisions == []


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "revision_list_missing_female_not_disproof",
            "public_tree_revision_list_overreach",
        ],
        "embedded_errors": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = revision_list_household.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_tree_17_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_17_revision_list_household_interpretation",
            "--output",
            "reports/eval/test_phase_26_5_tree_17_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_17_revision_list_household_interpretation" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_5_tree_17_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
