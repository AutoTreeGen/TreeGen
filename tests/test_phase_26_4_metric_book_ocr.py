"""Tests for Phase 26.4 metric book OCR repair detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import metric_book_ocr
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_16_PATH = REPO_ROOT / "data/test_corpus/trees/tree_16_metric_book_ocr_extraction_errors.json"


def load_tree_16() -> dict[str, Any]:
    data = json.loads(TREE_16_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_16_emits_metric_book_ocr_flags() -> None:
    output = run_tree(load_tree_16())

    assert "ocr_month_march_may_confusion" in output["engine_flags"]
    assert "ocr_kamenetsky_kaminsky_false_variant" in output["engine_flags"]
    assert "metric_book_gender_column_misread" in output["engine_flags"]
    assert "ocr_rabinovich_raskin_false_mother" in output["engine_flags"]
    assert "modern_place_normalization_lost_jurisdiction" in output["engine_flags"]
    assert "ocr_created_duplicate_profile" in output["engine_flags"]
    assert "online_tree_ocr_error_propagation" in output["engine_flags"]
    assert "primary_image_overrides_ocr_derivative" in output["engine_flags"]


def test_tree_16_marks_ocr_assertions_true() -> None:
    output = run_tree(load_tree_16())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_16_001"] is True
    assert evaluation_results["eval_16_002"] is True
    assert evaluation_results["eval_16_003"] is True
    assert evaluation_results["eval_16_004"] is True
    assert evaluation_results["eval_16_005"] is True


def test_tree_16_outputs_merge_and_quarantine_decisions() -> None:
    output = run_tree(load_tree_16())

    assert any(item.get("merge_pair") == ["I3", "I7"] for item in output["merge_decisions"])
    assert any(item.get("claim_type") == "birth_date" for item in output["quarantined_claims"])
    assert any(item.get("claim_type") == "surname_branch" for item in output["quarantined_claims"])
    assert any(item.get("claim_type") == "mother_identity" for item in output["quarantined_claims"])
    assert any(
        item.get("claim_type") == "online_tree_derivative_fact"
        for item in output["quarantined_claims"]
    )


def test_tree_16_confirms_rabinovich_mother() -> None:
    output = run_tree(load_tree_16())

    assert any(
        item.get("claim_type") == "mother"
        and item.get("object_name") == "Sura /Rabinovich/"
        and item.get("status") == "confirmed"
        for item in output["relationship_claims"]
    )


def test_detector_does_not_fire_without_ocr_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_ocr_errors",
        "embedded_errors": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = metric_book_ocr.detect(tree)

    assert result.engine_flags == []
    assert result.merge_decisions == []
    assert result.quarantined_claims == []
    assert result.evaluation_results == {}


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "ocr_month_march_may_confusion",
            "primary_image_overrides_ocr_derivative",
        ],
        "embedded_errors": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = metric_book_ocr.detect(tree)

    assert result.engine_flags == []
    assert result.merge_decisions == []


def test_tree_16_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_16_metric_book_ocr_extraction_errors",
            "--output",
            "reports/eval/test_phase_26_4_tree_16_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_16_metric_book_ocr_extraction_errors" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_4_tree_16_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
