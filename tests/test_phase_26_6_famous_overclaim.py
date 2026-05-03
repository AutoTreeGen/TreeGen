"""Tests for Phase 26.6 famous-relative overclaim filter."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from inference_engine.detectors import famous_overclaim
from inference_engine.engine import run_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
TREE_19_PATH = (
    REPO_ROOT
    / "data/test_corpus/trees/tree_19_famous_relative_royal_rabbinical_overclaim_filter.json"
)


def load_tree_19() -> dict[str, Any]:
    data = json.loads(TREE_19_PATH.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", data)


def test_tree_19_emits_famous_overclaim_flags() -> None:
    output = run_tree(load_tree_19())

    assert "royal_rashi_king_david_public_tree_chain" in output["engine_flags"]
    assert "rabbinical_schneerson_to_baal_shem_tov" in output["engine_flags"]
    assert "rabbinical_title_or_surname_as_proof" in output["engine_flags"]
    assert "tiny_dna_match_used_for_medieval_descent" in output["engine_flags"]
    assert "same_name_rabbinical_surname_false_merge" in output["engine_flags"]
    assert "public_tree_famous_descent_no_primary_bridge" in output["engine_flags"]
    assert "famous_descent_quarantine_required" in output["engine_flags"]


def test_tree_19_marks_all_assertions_true() -> None:
    output = run_tree(load_tree_19())
    evaluation_results = output["evaluation_results"]

    assert evaluation_results["eval_19_001"] is True
    assert evaluation_results["eval_19_002"] is True
    assert evaluation_results["eval_19_003"] is True
    assert evaluation_results["eval_19_004"] is True
    assert evaluation_results["eval_19_005"] is True


def test_tree_19_confirms_local_branch_only() -> None:
    output = run_tree(load_tree_19())

    assert any(
        item.get("claim_type") == "local_branch"
        and item.get("status") == "confirmed"
        and item.get("scope") == "local Brest Soloveichik branch only"
        for item in output["relationship_claims"]
    )


def test_tree_19_rejects_famous_descent_claims() -> None:
    output = run_tree(load_tree_19())

    assert any(item.get("claim_type") == "famous_descent" for item in output["quarantined_claims"])
    assert any(
        item.get("claim_type") == "rabbinical_dynasty_descent"
        for item in output["quarantined_claims"]
    )
    assert any(
        item.get("claim_type") == "dna_to_medieval_descent" for item in output["quarantined_claims"]
    )
    assert any(
        item.get("claim_type") == "public_tree_famous_descent"
        for item in output["quarantined_claims"]
    )


def test_tree_19_rejects_same_name_rabbinical_merge() -> None:
    output = run_tree(load_tree_19())

    assert any(
        item.get("merge_pair") == ["I5", "I12"]
        and item.get("status") == "Rejected"
        and item.get("action") == "do_not_merge"
        for item in output["merge_decisions"]
    )


def test_detector_does_not_fire_without_famous_context() -> None:
    tree = {
        "tree_id": "tree_synthetic_no_famous_context",
        "embedded_errors": [
            {
                "type": "fabrication",
                "subtype": "generic_unsourced_parent",
                "expected_flag": "royal_rashi_king_david_public_tree_chain",
            }
        ],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = famous_overclaim.detect(tree)

    assert result.engine_flags == []
    assert result.quarantined_claims == []


def test_detector_does_not_copy_answer_key_without_input_evidence() -> None:
    tree = {
        "tree_id": "tree_synthetic_answer_key_only",
        "expected_engine_flags": [
            "royal_rashi_king_david_public_tree_chain",
            "famous_descent_quarantine_required",
        ],
        "embedded_errors": [],
        "input_archive_snippets": [],
        "evaluation_assertions": [],
    }

    result = famous_overclaim.detect(tree)

    assert result.engine_flags == []
    assert result.evaluation_results == {}


def test_tree_19_score_is_complete() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_eval.py",
            "--tree",
            "tree_19_famous_relative_royal_rabbinical_overclaim_filter",
            "--output",
            "reports/eval/test_phase_26_6_tree_19_report.json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tree_19_famous_relative_royal_rabbinical_overclaim_filter" in completed.stdout

    report_path = REPO_ROOT / "reports/eval/test_phase_26_6_tree_19_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    score = report["tree_results"][0]["score"]

    assert score == 1.0
