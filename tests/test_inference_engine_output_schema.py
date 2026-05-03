"""Phase 26.1 — engine output schema + baseline contract tests.

Покрывает:

- ``run_tree`` возвращает все required top-level keys (см. ADR-0084).
- ``tree_id`` round-trip'ит вход → выход.
- ``evaluation_results`` содержит assertion_id-ключи из tree-fixture'а.
- Baseline НЕ читерит: ни ``engine_flags``, ни assertion-status не
  совпадают с ``expected_*`` фиксации tree-fixture'а.
- Output проходит Pydantic-валидацию ``EngineOutput``.
- Trees 01-03 (legacy формат без ``assertion_id``) получают синтетические
  ID вида ``eval_NN_NNN``.
- Bad input (no tree_id) → ``ValueError``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from inference_engine.engine import run_tree
from inference_engine.output_schema import (
    REQUIRED_OUTPUT_KEYS,
    EngineOutput,
    validate_output,
)
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TREES_DIR = _REPO_ROOT / "data" / "test_corpus" / "trees"


def _load(name: str) -> dict:
    return json.loads((_TREES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_output_has_all_required_top_level_keys() -> None:
    tree = _load("tree_11_unknown_father_npe_dna_contradiction.json")
    out = run_tree(tree)
    assert REQUIRED_OUTPUT_KEYS.issubset(out.keys()), (
        f"missing keys: {REQUIRED_OUTPUT_KEYS - out.keys()}"
    )


def test_output_does_not_have_extra_keys() -> None:
    """Top-level keys должны быть строго REQUIRED_OUTPUT_KEYS — ни больше, ни меньше."""
    tree = _load("tree_11_unknown_father_npe_dna_contradiction.json")
    out = run_tree(tree)
    assert set(out.keys()) == REQUIRED_OUTPUT_KEYS


def test_output_preserves_tree_id() -> None:
    tree = _load("tree_05_brest_litovsk_holocaust_gap_reconstruction.json")
    out = run_tree(tree)
    assert out["tree_id"] == "tree_05_brest_litovsk_holocaust_gap_reconstruction"


def test_output_validates_against_pydantic_schema() -> None:
    """Любой output должен проходить Pydantic-валидацию ``EngineOutput``."""
    for path in sorted(_TREES_DIR.glob("tree_*.json")):
        tree = json.loads(path.read_text(encoding="utf-8"))
        out = run_tree(tree)
        # Не должно бросить ValidationError.
        model = validate_output(out)
        assert isinstance(model, EngineOutput)
        assert model.tree_id == tree["tree_id"]


# ---------------------------------------------------------------------------
# evaluation_results / assertion_id mechanics
# ---------------------------------------------------------------------------


def test_evaluation_results_keys_match_assertion_ids_for_modern_trees() -> None:
    """Trees 04-20 хранят explicit ``assertion_id`` — output keys должны 1:1."""
    tree = _load("tree_11_unknown_father_npe_dna_contradiction.json")
    out = run_tree(tree)
    expected = {a["assertion_id"] for a in tree["evaluation_assertions"]}
    assert set(out["evaluation_results"].keys()) == expected


def test_assertion_id_synthesised_for_legacy_trees_1_3() -> None:
    """Trees 01-03 без ``assertion_id`` → engine синтезирует ``eval_01_NNN``."""
    tree = _load("tree_01_pale_levitin_npe_resolution.json")
    out = run_tree(tree)
    keys = list(out["evaluation_results"].keys())
    assert len(keys) == len(tree["evaluation_assertions"])
    # Pattern: ``eval_01_001``, ``eval_01_002``, …
    for idx, key in enumerate(keys, start=1):
        assert key == f"eval_01_{idx:03d}", keys


def test_evaluation_results_is_dict_str_to_bool() -> None:
    tree = _load("tree_15_gedcom_safe_merge_conflicting_sources.json")
    out = run_tree(tree)
    for k, v in out["evaluation_results"].items():
        assert isinstance(k, str), f"non-str key: {k!r}"
        assert isinstance(v, bool), f"non-bool value at {k}: {v!r}"


# ---------------------------------------------------------------------------
# Anti-cheat
# ---------------------------------------------------------------------------

# Tree без подходящего детектора в Phase 26.2 (patronymic disambiguation —
# DNA слабая, no NPE-сигнал). По мере добавления детекторов в Phase 26.3+
# pivot на tree, который ещё не покрыт.
_UNCOVERED_TREE = "tree_07_patronymic_vs_surname_disambiguation.json"


def test_uncovered_tree_does_not_emit_expected_engine_flags() -> None:
    """ADR-0084 §"Anti-cheat": engine не должен auto-pass'ить flag_score
    путём копирования ``expected_engine_flags`` в output. Проверяем на
    tree без подходящего детектора — output должен оставаться пустым.
    """
    tree = _load(_UNCOVERED_TREE)
    out = run_tree(tree)
    expected = set(tree["expected_engine_flags"])
    actual = set(out["engine_flags"])
    assert actual & expected == set(), (
        "engine must not auto-pass by copying expected_engine_flags into output"
    )


def test_uncovered_tree_evaluation_results_all_false() -> None:
    """Tree без активного детектора → все assertion'ы помечены False."""
    tree = _load(_UNCOVERED_TREE)
    out = run_tree(tree)
    assert out["evaluation_results"], "expected non-empty evaluation_results"
    assert all(v is False for v in out["evaluation_results"].values())


def test_uncovered_tree_lists_are_empty() -> None:
    """Tree без активного детектора: relationship_claims / merge_decisions
    / etc — пустые."""
    tree = _load(_UNCOVERED_TREE)
    out = run_tree(tree)
    for key in (
        "engine_flags",
        "relationship_claims",
        "merge_decisions",
        "place_corrections",
        "quarantined_claims",
        "sealed_set_candidates",
    ):
        assert out[key] == [], f"{key} expected empty for uncovered tree, got {out[key]!r}"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_run_tree_raises_on_missing_tree_id() -> None:
    with pytest.raises(ValueError, match="tree_id"):
        run_tree({})


def test_run_tree_raises_on_empty_tree_id() -> None:
    with pytest.raises(ValueError, match="tree_id"):
        run_tree({"tree_id": ""})


def test_run_tree_raises_on_non_string_tree_id() -> None:
    with pytest.raises(ValueError, match="tree_id"):
        run_tree({"tree_id": 123})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_engine_output_forbids_extra_top_level_keys() -> None:
    """``EngineOutput`` использует ``extra='forbid'``; неизвестный ключ → ValidationError."""
    tree = _load("tree_05_brest_litovsk_holocaust_gap_reconstruction.json")
    out = run_tree(tree)
    out["unknown_key"] = "should be rejected"
    with pytest.raises(ValidationError):
        validate_output(out)
