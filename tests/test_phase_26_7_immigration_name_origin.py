"""Tests for Phase 26.7 immigration name/origin detector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import immigration_name_origin
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_18_PATH = (
    REPO_ROOT / "data/test_corpus/trees/tree_18_immigration_name_change_myth_and_wrong_origin.json"
)


def load_tree_18() -> dict[str, Any]:
    data = json.loads(TREE_18_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_18_emits_immigration_flags() -> None:
    output = run_tree(load_tree_18())

    assert "ellis_island_name_change_myth" in output["engine_flags"]
    assert "immigration_same_name_wrong_origin_attachment" in output["engine_flags"]
    assert "surname_only_parent_assignment" in output["engine_flags"]
    assert "family_story_contradicted_by_primary_records" in output["engine_flags"]
    assert "small_galician_surname_collision" in output["engine_flags"]
    assert "wrong_origin_place_assignment" in output["engine_flags"]
    assert "chain_migration_contact_supports_identity" in output["engine_flags"]
    assert "alias_history_not_new_person" in output["engine_flags"]


def test_tree_18_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_18())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_18_001"] is True
    assert evaluation_results["eval_18_002"] is True
    assert evaluation_results["eval_18_003"] is True
    assert evaluation_results["eval_18_004"] is True
    assert evaluation_results["eval_18_005"] is True


def test_tree_18_confirms_brest_origin_and_parents() -> None:
    output = run_tree(load_tree_18())

    assert any(
        item.get("claim_type") == "origin"
        and item.get("accepted_value") == "Brest-Litovsk"
        and item.get("status") == "confirmed"
        for item in output["relationship_claims"]
    )
    assert any(
        item.get("claim_type") == "parents"
        and item.get("father_name") == "Leib /Friedman/"
        and item.get("mother_name") == "Sura /Levitin/"
        and item.get("status") == "confirmed"
        for item in output["relationship_claims"]
    )


def test_tree_18_preserves_alias_history_without_split() -> None:
    output = run_tree(load_tree_18())

    assert any(
        item.get("claim_type") == "alias_history"
        and "Morris Freedman" in item.get("aliases", [])
        and item.get("status") == "confirmed"
        for item in output["relationship_claims"]
    )


def test_tree_18_rejects_wrong_origin_merge() -> None:
    output = run_tree(load_tree_18())

    assert any(
        item.get("merge_pair") == ["I4", "I6"]
        and item.get("status") == "Rejected"
        and item.get("action") == "do_not_merge"
        for item in output["merge_decisions"]
    )


def test_detector_does_not_fire_without_immigration_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_immigration_context",
        "embedded_errors": [
            {
                "type": "source_quality",
                "expected_flag": "family_story_contradicted_by_primary_records",
            }
        ],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = immigration_name_origin.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "ellis_island_name_change_myth",
            "alias_history_not_new_person",
        ],
        "embedded_errors": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = immigration_name_origin.detect(tree)

    assert result.engine_flags == []
    assert result.relationship_claims == []


def test_tree_18_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_18_immigration_name_change_myth_and_wrong_origin",
            "--output",
            "reports/eval/test_phase_26_7_tree_18_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_18_immigration_name_change_myth_and_wrong_origin" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_7_tree_18_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
