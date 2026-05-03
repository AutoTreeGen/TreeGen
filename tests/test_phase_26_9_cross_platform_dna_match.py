"""Tests for Phase 26.9 cross-platform DNA match resolver."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import cross_platform_dna_match
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_09_PATH = REPO_ROOT / "data/test_corpus/trees/tree_09_cross_platform_dna_match_resolver.json"


def load_tree_09() -> dict[str, Any]:
    data = json.loads(TREE_09_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_09_emits_cross_platform_flags() -> None:
    output = run_tree(load_tree_09())

    assert "same_name_different_person" in output["engine_flags"]
    assert "shared_cluster_not_identity" in output["engine_flags"]
    assert "surname_only_identity_merge_risk" in output["engine_flags"]
    assert "public_tree_same_cluster_person_merge_error" in output["engine_flags"]
    assert "endogamy_small_segment_overuse" in output["engine_flags"]
    assert "cross_platform_identity_resolved" in output["engine_flags"]
    assert "kit_id_email_hash_match_confirmed" in output["engine_flags"]


def test_tree_09_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_09())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_09_001"] is True
    assert evaluation_results["eval_09_002"] is True
    assert evaluation_results["eval_09_003"] is True
    assert evaluation_results["eval_09_004"] is True
    assert evaluation_results["eval_09_005"] is True


def test_tree_09_resolves_adrienne_cross_platform_cluster() -> None:
    output = run_tree(load_tree_09())

    assert any(
        item.get("merge_pair") == ["dna_901", "dna_902", "dna_903"]
        and item.get("status") == "Confirmed"
        and item.get("action") == "resolve_as_same_dna_match_person"
        for item in output["merge_decisions"]
    )


def test_tree_09_keeps_geoff_as_cluster_not_identity() -> None:
    output = run_tree(load_tree_09())

    assert any(
        item.get("merge_pair") == ["dna_901", "dna_905"]
        and item.get("status") == "Rejected"
        and item.get("action") == "same_cluster_not_same_person"
        for item in output["merge_decisions"]
    )


def test_tree_09_rejects_ftdna_same_name_wrong_person() -> None:
    output = run_tree(load_tree_09())

    assert any(
        item.get("merge_pair") == ["dna_901", "dna_904"]
        and item.get("status") == "Rejected"
        and item.get("action") == "do_not_merge"
        for item in output["merge_decisions"]
    )


def test_tree_09_confirms_levitin_kaplan_bridge() -> None:
    output = run_tree(load_tree_09())

    assert any(
        item.get("claim_type") == "family_cluster_relationship"
        and item.get("status") == "confirmed"
        and item.get("cluster") == "Levitin/Kaplan/Brest"
        for item in output["relationship_claims"]
    )


def test_detector_does_not_fire_without_cross_platform_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_cross_platform_context",
        "embedded_errors": [
            {
                "type": "cross_platform_identity_error",
                "expected_flag": "same_name_different_person",
            }
        ],
        "input_dna_matches": [
            {
                "match_id": "dna_x",
                "platform": "AncestryDNA",
            }
        ],
        "evaluation_assertions": [],
    }

    result = cross_platform_dna_match.detect(tree)

    assert result.engine_flags == []
    assert result.merge_decisions == []


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "cross_platform_identity_resolved",
            "kit_id_email_hash_match_confirmed",
        ],
        "embedded_errors": [],
        "input_dna_matches": [],
        "evaluation_assertions": [],
    }

    result = cross_platform_dna_match.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_tree_09_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_09_cross_platform_dna_match_resolver",
            "--output",
            "reports/eval/test_phase_26_9_tree_09_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_09_cross_platform_dna_match_resolver" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_9_tree_09_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
