"""Phase 27.1 — evidence primitives, anti-cheat helper, cheat-surface pin.

Покрывает (ADR-0097):

§A. Extractor unit-tests — пустой / wrong-type / non-dict items.
§B. Strip-семантика — answer-key sub-поля физически удалены из
    returned items; input tree не мутируется.
§C. Property: на 20-tree corpus extractor output не содержит ни одного
    из ANSWER_KEY_NESTED_FIELDS sub-ключей.
§D. ``poison_answer_key`` / ``assert_detector_ignores_answer_key``
    self-tests.
§E. **Pinned cheat surface** — global set detector'ов, чей output
    меняется при poisoning answer-key полей. Это diagnostic, не assert
    that-cheat-is-zero — Phase 27.1 ships current state, Phase 27.2+
    migrations убирают detector'ов из set'а по одному.
§F. **Corpus regression** — per-tree score parity vs pre-PR baseline.
§G. Import-cycle guard.
"""

from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from typing import Any

import pytest
from _evidence_helpers import (
    ANSWER_KEY_GARBAGE,
    assert_detector_ignores_answer_key,
    poison_answer_key,
)
from inference_engine.detectors.registry import all_detectors
from inference_engine.detectors.result import DetectorResult
from inference_engine.evidence import (
    ANSWER_KEY_NESTED_FIELDS,
    ANSWER_KEY_TOP_LEVEL_FIELDS,
    archive_snippets,
    combined_snippet_text,
    dna_matches,
    embedded_errors,
    user_assertions,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TREES_DIR = _REPO_ROOT / "data" / "test_corpus" / "trees"

_EXTRACTORS_BY_TREE_KEY = {
    "embedded_errors": embedded_errors,
    "input_archive_snippets": archive_snippets,
    "input_dna_matches": dna_matches,
    "input_user_assertions": user_assertions,
}


# ---------------------------------------------------------------------------
# §A. Extractor smoke
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extractor",
    [embedded_errors, archive_snippets, dna_matches, user_assertions],
)
def test_extractor_returns_empty_list_on_missing_key(extractor: Any) -> None:
    assert extractor({}) == []


@pytest.mark.parametrize(
    "extractor",
    [embedded_errors, archive_snippets, dna_matches, user_assertions],
)
@pytest.mark.parametrize("bad_value", [None, "not-a-list", 42, {"x": 1}])
def test_extractor_returns_empty_list_on_non_list_value(extractor: Any, bad_value: Any) -> None:
    tree = {
        "embedded_errors": bad_value,
        "input_archive_snippets": bad_value,
        "input_dna_matches": bad_value,
        "input_user_assertions": bad_value,
    }
    assert extractor(tree) == []


@pytest.mark.parametrize(
    ("tree_key", "extractor"),
    list(_EXTRACTORS_BY_TREE_KEY.items()),
)
def test_extractor_filters_non_dict_items(tree_key: str, extractor: Any) -> None:
    tree = {tree_key: [{"good": True}, 42, "x", None, {"also": "good"}]}
    out = extractor(tree)
    assert out == [{"good": True}, {"also": "good"}]


# ---------------------------------------------------------------------------
# §B. Strip semantics
# ---------------------------------------------------------------------------


def test_embedded_errors_strips_expected_flag_and_reason_and_confidence() -> None:
    tree = {
        "embedded_errors": [
            {
                "type": "x",
                "subtype": "y",
                "persons": ["I1"],
                "expected_flag": "F",
                "expected_confidence_when_flagged": 0.87,
                "reason": "answer-key author description",
            }
        ]
    }
    [item] = embedded_errors(tree)
    assert "expected_flag" not in item
    assert "expected_confidence_when_flagged" not in item
    assert "reason" not in item
    assert item == {"type": "x", "subtype": "y", "persons": ["I1"]}


def test_archive_snippets_strips_expected_use() -> None:
    tree = {
        "input_archive_snippets": [
            {"snippet_id": "s1", "transcription_excerpt": "T", "expected_use": "answer"}
        ]
    }
    [item] = archive_snippets(tree)
    assert "expected_use" not in item
    assert item["transcription_excerpt"] == "T"


def test_dna_matches_strips_expected_link() -> None:
    tree = {"input_dna_matches": [{"match_id": "m1", "shared_cm": 1500, "expected_link": "answer"}]}
    [item] = dna_matches(tree)
    assert "expected_link" not in item
    assert item["shared_cm"] == 1500


def test_user_assertions_strips_nothing() -> None:
    tree = {
        "input_user_assertions": [
            {
                "person_id": "I1",
                "assertion": "X",
                "scope": "biological_parentage",
                "evidence": "DNA cluster",
            }
        ]
    }
    [item] = user_assertions(tree)
    assert item == {
        "person_id": "I1",
        "assertion": "X",
        "scope": "biological_parentage",
        "evidence": "DNA cluster",
    }


def test_extractors_do_not_mutate_input_tree() -> None:
    tree = {
        "embedded_errors": [{"type": "x", "expected_flag": "F", "reason": "R"}],
        "input_archive_snippets": [{"snippet_id": "s", "expected_use": "U"}],
        "input_dna_matches": [{"match_id": "m", "expected_link": "L"}],
    }
    snapshot = copy.deepcopy(tree)
    embedded_errors(tree)
    archive_snippets(tree)
    dna_matches(tree)
    assert tree == snapshot, "extractor mutated input tree"


def test_returned_dict_is_independent_at_top_level() -> None:
    """Caller'а нельзя позволять мутировать tree через top-level keys
    returned dict'а (защита от accidental shared-state bugs)."""
    tree = {"embedded_errors": [{"type": "x", "persons": ["I1"]}]}
    [item] = embedded_errors(tree)
    item["type"] = "MUTATED"
    assert tree["embedded_errors"][0]["type"] == "x"


# ---------------------------------------------------------------------------
# §C. Property — corpus-wide
# ---------------------------------------------------------------------------


def test_corpus_extractor_output_contains_no_answer_key_subfields() -> None:
    """На всех 20 fixture trees output extractor'ов не содержит ни одного
    answer-key sub-поля. Это корневой contract Phase 27.1."""
    for tree_path in sorted(_TREES_DIR.glob("tree_*.json")):
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        for tree_key, forbidden in ANSWER_KEY_NESTED_FIELDS.items():
            if not forbidden:
                continue
            extractor = _EXTRACTORS_BY_TREE_KEY[tree_key]
            for item in extractor(tree):
                leaked = forbidden & item.keys()
                assert not leaked, (
                    f"{tree_path.name}: {tree_key} extractor leaked "
                    f"answer-key keys {leaked} in item {item!r}"
                )


# ---------------------------------------------------------------------------
# §D. combined_snippet_text + helper self-tests
# ---------------------------------------------------------------------------


def test_combined_snippet_text_default_fields() -> None:
    snippets = [
        {
            "snippet_id": "s1",
            "transcription_excerpt": "Birth record",
            "type": "civil_BDM_birth",
            "language": "ru",
        },
        {
            "snippet_id": "s2",
            "transcription_excerpt": "OCR raw",
            "type": "ocr_output_raw",
            "language": "en",
        },
    ]
    text = combined_snippet_text(snippets)
    assert "Birth record" in text
    assert "civil_BDM_birth" in text
    assert "ru" in text
    assert "OCR raw" in text


def test_combined_snippet_text_custom_fields_only() -> None:
    snippets = [{"transcription_excerpt": "T", "type": "x", "language": "ru"}]
    text = combined_snippet_text(snippets, fields=("transcription_excerpt",))
    assert text == "T"


def test_combined_snippet_text_skips_non_string() -> None:
    snippets = [{"transcription_excerpt": 42, "type": None, "language": "en"}]
    text = combined_snippet_text(snippets)
    assert text == "en"


def test_combined_snippet_text_empty_input() -> None:
    assert combined_snippet_text([]) == ""


def test_poison_answer_key_replaces_top_level_fields() -> None:
    tree = {
        "tree_id": "t1",
        "input_gedcom_excerpt": "0 HEAD\n0 TRLR",
        "expected_engine_flags": ["should_not_read"],
        "expected_confidence_outputs": {"x": 1},
        "ground_truth_annotations": {"y": 2},
        "expected_reasoning_chain": ["step1"],
    }
    poisoned = poison_answer_key(tree)
    assert poisoned["tree_id"] == "t1"
    assert poisoned["input_gedcom_excerpt"] == "0 HEAD\n0 TRLR"
    for k in ANSWER_KEY_TOP_LEVEL_FIELDS:
        assert poisoned[k] == ANSWER_KEY_GARBAGE


def test_poison_answer_key_replaces_nested_fields() -> None:
    tree = {
        "embedded_errors": [{"type": "x", "expected_flag": "F", "reason": "R"}],
        "input_archive_snippets": [{"snippet_id": "s", "expected_use": "U"}],
        "input_dna_matches": [{"match_id": "m", "shared_cm": 100, "expected_link": "L"}],
    }
    poisoned = poison_answer_key(tree)
    err = poisoned["embedded_errors"][0]
    assert err["type"] == "x"
    assert err["expected_flag"] == ANSWER_KEY_GARBAGE
    assert err["reason"] == ANSWER_KEY_GARBAGE
    assert poisoned["input_archive_snippets"][0]["expected_use"] == ANSWER_KEY_GARBAGE
    assert poisoned["input_dna_matches"][0]["expected_link"] == ANSWER_KEY_GARBAGE
    # Original tree не тронут.
    assert tree["embedded_errors"][0]["expected_flag"] == "F"


def test_assert_detector_ignores_answer_key_passes_for_pure_detector() -> None:
    def pure(tree: dict[str, Any]) -> DetectorResult:
        return DetectorResult()

    tree = {"tree_id": "t", "expected_engine_flags": ["x"]}
    assert_detector_ignores_answer_key(pure, tree)


def test_assert_detector_ignores_answer_key_fails_for_top_level_cheater() -> None:
    def cheater(tree: dict[str, Any]) -> DetectorResult:
        flags = tree.get("expected_engine_flags") or []
        return DetectorResult(engine_flags=list(flags))

    tree = {"tree_id": "t", "expected_engine_flags": ["leaked_flag"]}
    with pytest.raises(AssertionError, match="answer-key"):
        assert_detector_ignores_answer_key(cheater, tree)


def test_assert_detector_ignores_answer_key_fails_for_nested_cheater() -> None:
    def nested_cheater(tree: dict[str, Any]) -> DetectorResult:
        flags = []
        for err in tree.get("embedded_errors") or []:
            ef = err.get("expected_flag")
            if isinstance(ef, str):
                flags.append(ef)
        return DetectorResult(engine_flags=flags)

    tree = {
        "tree_id": "t",
        "embedded_errors": [{"type": "x", "expected_flag": "leaked_flag"}],
    }
    with pytest.raises(AssertionError, match="answer-key"):
        assert_detector_ignores_answer_key(nested_cheater, tree)


# ---------------------------------------------------------------------------
# §E. Pinned cheat surface — diagnostic
# ---------------------------------------------------------------------------

# Зафиксировано empirically на момент Phase 27.1 PR. Каждый detector в
# этом set'е читает answer-key поля (top-level или nested) хотя бы на
# одном tree из corpus'а. Phase 27.2+ migration'ы убирают detector'ы
# отсюда по одному:
#
#   - PR migrating ``historical_place_jurisdiction`` → удаляет его
#     отсюда + добавляет ``assert_detector_ignores_answer_key`` в свой
#     test. Если migration оставляет cheat — этот тест падает на CI.
#   - Если новый detector добавляется и cheat'ит — set drift'ит
#     дальше, тест падает, PR блокируется.
#
# Анти-cheat это диагностика, не absolute assert. Phase 27.1 ships
# current cheat surface intact.
KNOWN_ANSWER_KEY_CONSUMERS: frozenset[str] = frozenset(
    {
        "cross_platform_dna_match",
        "historical_place_jurisdiction",
        "mennonite_founder_loop",
        "metric_book_ocr",
        "revision_list_household",
        "sephardic_mizrahi_crossover",
    }
)


def _detector_module_name(detect_fn: Any) -> str:
    return detect_fn.__module__.rsplit(".", 1)[-1]


def test_pinned_cheat_surface_matches_reality() -> None:
    """Сверяет pinned ``KNOWN_ANSWER_KEY_CONSUMERS`` с фактическим
    cheat surface'ом на текущем corpus'е.

    Прогоняет каждый registered detector на каждом из 20 trees
    дважды (clean + poisoned). Если output меняется — detector в
    cheat-set'е. Result сравнивается с pinned constant'ом.
    """
    detectors = all_detectors()
    actual: set[str] = set()
    for tree_path in sorted(_TREES_DIR.glob("tree_*.json")):
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        poisoned = poison_answer_key(tree)
        for det in detectors:
            name = _detector_module_name(det)
            if name in actual:
                continue
            clean_out = det(copy.deepcopy(tree))
            poisoned_out = det(copy.deepcopy(poisoned))
            if clean_out != poisoned_out:
                actual.add(name)

    added = actual - KNOWN_ANSWER_KEY_CONSUMERS
    removed = KNOWN_ANSWER_KEY_CONSUMERS - actual
    assert actual == KNOWN_ANSWER_KEY_CONSUMERS, (
        f"cheat surface drifted:\n"
        f"  newly cheating (must fix or add to pin): {sorted(added)}\n"
        f"  stopped cheating (update pin in migration PR): {sorted(removed)}"
    )


# ---------------------------------------------------------------------------
# §F. Corpus regression — per-tree scores unchanged
# ---------------------------------------------------------------------------

# Pinned from `uv run python scripts/run_eval.py` on main as of the
# pre-PR baseline (см. reports/eval/phase_27_1_baseline.json).
# Phase 27.1 — additive only; ни один tree'ный score не должен
# измениться.
PHASE_27_1_BASELINE_SCORES: dict[str, float] = {
    "tree_01_pale_levitin_npe_resolution": 0.0,
    "tree_02_mennonite_batensky_fictional_bridge": 0.0,
    "tree_03_friedman_raskes_identity_resolution": 0.0,
    "tree_04_voikhansky_kamenetsky_viral_tree_contamination": 0.1286,
    "tree_05_brest_litovsk_holocaust_gap_reconstruction": 0.1,
    "tree_06_rabbi_kamenetsky_hypothesis_not_confirmed": 0.1286,
    "tree_07_patronymic_vs_surname_disambiguation": 0.1,
    "tree_08_maiden_vs_married_name_resolution": 0.1286,
    "tree_09_cross_platform_dna_match_resolver": 1.0,
    "tree_10_historical_place_jurisdiction_resolution": 1.0,
    "tree_11_unknown_father_npe_dna_contradiction": 0.5357,
    "tree_12_ashkenazi_endogamy_multi_path_relationship": 0.1,
    "tree_13_mennonite_colony_founder_loop_ambiguity": 1.0,
    "tree_14_sephardic_mizrahi_crossover_false_ashkenazi_merge": 1.0,
    "tree_15_gedcom_safe_merge_conflicting_sources": 1.0,
    "tree_16_metric_book_ocr_extraction_errors": 1.0,
    "tree_17_revision_list_household_interpretation": 1.0,
    "tree_18_immigration_name_change_myth_and_wrong_origin": 0.1,
    "tree_19_famous_relative_royal_rabbinical_overclaim_filter": 0.1,
    "tree_20_full_pipeline_sealed_set_contradiction_resolution": 0.1364,
}


def test_corpus_eval_scores_unchanged(tmp_path: Path) -> None:
    """Per-tree score parity vs pre-PR baseline.

    Phase 27.1 — additive only. Если этот тест падает, либо мы
    нечаянно сломали detector logic, либо изменили scoring. Diff
    output показывает какой именно tree drift'ит.
    """
    out = tmp_path / "phase_27_1_eval.json"
    completed = subprocess.run(
        [sys.executable, "scripts/run_eval.py", "--output", str(out)],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "overall:" in completed.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    actual = {r["tree_id"]: r["score"] for r in report["tree_results"]}

    drifts: list[str] = []
    for tree_id, expected in PHASE_27_1_BASELINE_SCORES.items():
        got = actual.get(tree_id)
        if got != expected:
            drifts.append(f"  {tree_id}: baseline={expected}, got={got}")

    assert not drifts, "per-tree eval scores drifted from Phase 27.1 baseline:\n" + "\n".join(
        drifts
    )


# ---------------------------------------------------------------------------
# §G. Import-cycle guard
# ---------------------------------------------------------------------------


def test_evidence_module_imports_cleanly() -> None:
    importlib.import_module("inference_engine.evidence")
    importlib.import_module("inference_engine.evidence.primitives")
    importlib.import_module("inference_engine.evidence.extractors")
    importlib.import_module("inference_engine.evidence.builders")
    importlib.import_module("inference_engine.detectors.registry")


def test_evidence_does_not_import_detectors() -> None:
    """``evidence`` — leaf package: не должен зависеть от ``detectors``."""
    import inference_engine.evidence as ev

    src = Path(ev.__file__).parent
    for py_file in src.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        assert "from inference_engine.detectors" not in text, (
            f"{py_file.name} imports from detectors — would create cycle"
        )
        assert "import inference_engine.detectors" not in text, (
            f"{py_file.name} imports detectors — would create cycle"
        )
