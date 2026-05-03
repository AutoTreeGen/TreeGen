"""Tests for Phase 26.8 historical-place jurisdiction detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import historical_place_jurisdiction
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_10_PATH = (
    REPO_ROOT / "data/test_corpus/trees/tree_10_historical_place_jurisdiction_resolution.json"
)


def load_tree_10() -> dict[str, Any]:
    data = json.loads(TREE_10_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_10_emits_historical_place_flags() -> None:
    output = run_tree(load_tree_10())

    assert "modern_country_for_pre1917_record" in output["engine_flags"]
    assert "partition_jurisdiction_confusion" in output["engine_flags"]
    assert "old_name_used_for_wrong_period" in output["engine_flags"]
    assert "danzig_gdansk_period_error" in output["engine_flags"]
    assert "mennonite_colony_generic_ukraine_error" in output["engine_flags"]
    assert "mennonite_jewish_boundary_error" in output["engine_flags"]
    assert "modern_place_normalization_lost_jurisdiction" in output["engine_flags"]
    assert "archive_routing_by_event_year_required" in output["engine_flags"]


def test_tree_10_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_10())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_10_001"] is True
    assert evaluation_results["eval_10_002"] is True
    assert evaluation_results["eval_10_003"] is True
    assert evaluation_results["eval_10_004"] is True
    assert evaluation_results["eval_10_005"] is True
    assert evaluation_results["eval_10_006"] is True


def test_tree_10_outputs_place_corrections() -> None:
    output = run_tree(load_tree_10())
    corrections = output["place_corrections"]

    assert any("Grodno Governorate" in item.get("accepted_value", "") for item in corrections)
    assert any("Congress Poland" in item.get("accepted_value", "") for item in corrections)
    assert any("West Prussia" in item.get("accepted_value", "") for item in corrections)
    assert any(
        "Molotschna Mennonite Colony" in item.get("accepted_value", "") for item in corrections
    )


def test_tree_10_keeps_mennonite_and_jewish_clusters_separate() -> None:
    output = run_tree(load_tree_10())

    assert any(
        item.get("claim_type") == "cluster_boundary" and item.get("status") == "kept_separate"
        for item in output["relationship_claims"]
    )


def test_detector_does_not_fire_without_place_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_place_context",
        "embedded_errors": [
            {
                "type": "place_jurisdiction_error",
                "expected_flag": "modern_country_for_pre1917_record",
            }
        ],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = historical_place_jurisdiction.detect(tree)

    assert result.engine_flags == []
    assert result.place_corrections == []


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "modern_country_for_pre1917_record",
            "archive_routing_by_event_year_required",
        ],
        "embedded_errors": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = historical_place_jurisdiction.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_tree_10_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_10_historical_place_jurisdiction_resolution",
            "--output",
            "reports/eval/test_phase_26_8_tree_10_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_10_historical_place_jurisdiction_resolution" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_8_tree_10_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
