"""Phase 26.1 — corpus discovery + evaluation runner tests.

Покрывает:

- Полнота корпуса (20 деревьев, нумерация 1-20).
- Filename ↔ tree_id consistency.
- Загрузка harness JSON + sanity на 20 test_cases.
- Вызов ``scripts/run_eval.py`` через subprocess: single-tree run пишет
  report и возвращает 0; missing tree_id даёт чистый non-zero exit.
- ``--fail-under`` действительно режет exit code (overall < threshold → 1).

См. ADR-0084 §"Acceptance".
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TREES_DIR = _REPO_ROOT / "data" / "test_corpus" / "trees"
_HARNESS_FILE = (
    _REPO_ROOT
    / "data"
    / "test_corpus"
    / "harness"
    / "autotreegen_evaluation_harness_trees1_20.json"
)
_RUNNER = _REPO_ROOT / "scripts" / "run_eval.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Запустить ``scripts/run_eval.py`` тем же интерпретатором, что и тесты."""
    return subprocess.run(
        [sys.executable, str(_RUNNER), *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )


def test_corpus_has_exactly_20_trees() -> None:
    """Brief: «discover all 20 tree JSON files»."""
    paths = sorted(_TREES_DIR.glob("tree_*.json"))
    assert len(paths) == 20, (
        f"expected 20 tree files in {_TREES_DIR}, got {len(paths)}: {[p.name for p in paths]}"
    )


def test_corpus_tree_numbers_form_contiguous_1_through_20() -> None:
    """tree_id-номера должны быть строго 1..20 без пропусков и дубликатов."""
    paths = sorted(_TREES_DIR.glob("tree_*.json"))
    nums: list[int] = []
    for p in paths:
        # ``tree_NN_<slug>.json`` — берём NN.
        prefix = p.stem.split("_")[1]
        assert prefix.isdigit(), f"{p.name}: expected numeric prefix"
        nums.append(int(prefix))
    assert nums == list(range(1, 21)), nums


def test_each_tree_id_matches_filename_stem() -> None:
    """``tree_id`` внутри JSON должен round-trip'ить с filename без расширения."""
    for path in sorted(_TREES_DIR.glob("tree_*.json")):
        tree = json.loads(path.read_text(encoding="utf-8"))
        assert tree["tree_id"] == path.stem, (
            f"{path.name}: tree_id={tree.get('tree_id')!r} does not match stem"
        )


def test_harness_loads_and_has_20_test_cases() -> None:
    """Brief: «harness loads, has 20 test cases»."""
    harness = json.loads(_HARNESS_FILE.read_text(encoding="utf-8"))
    assert "test_cases" in harness
    assert len(harness["test_cases"]) == 20
    # Sanity: каждый case — dict с tree_id.
    for tc in harness["test_cases"]:
        assert isinstance(tc, dict)
        tid = tc.get("tree_id")
        assert isinstance(tid, str)
        assert tid


def test_harness_test_case_tree_ids_match_corpus() -> None:
    """Harness test_cases должны 1:1 соответствовать корпусу tree files."""
    harness = json.loads(_HARNESS_FILE.read_text(encoding="utf-8"))
    harness_ids = {tc["tree_id"] for tc in harness["test_cases"]}
    corpus_ids = {p.stem for p in _TREES_DIR.glob("tree_*.json")}
    assert harness_ids == corpus_ids, (
        f"harness ↔ corpus mismatch:\n"
        f"  in harness only: {harness_ids - corpus_ids}\n"
        f"  in corpus only:  {corpus_ids - harness_ids}"
    )


def test_runner_writes_report_for_single_tree(tmp_path: Path) -> None:
    """``--tree X --output Y`` пишет валидный JSON-отчёт и завершается с 0."""
    out = tmp_path / "report.json"
    result = _run(
        "--tree",
        "tree_11_unknown_father_npe_dna_contradiction",
        "--output",
        str(out),
    )
    assert result.returncode == 0, f"runner failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert out.is_file(), f"expected report at {out}"

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["trees_evaluated"] == 1
    assert len(report["tree_results"]) == 1
    tree_result = report["tree_results"][0]
    assert tree_result["tree_id"] == "tree_11_unknown_father_npe_dna_contradiction"
    # Schema score — из harness'а: tree_11 имеет required_keys_present=True.
    assert tree_result["schema_score"] == pytest.approx(1.0)


def test_runner_full_corpus_writes_report(tmp_path: Path) -> None:
    """No-args run обходит все 20 деревьев."""
    out = tmp_path / "full_report.json"
    result = _run("--output", str(out))
    assert result.returncode == 0, f"runner failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["trees_evaluated"] == 20
    assert len(report["tree_results"]) == 20


def test_runner_unknown_tree_id_exits_2() -> None:
    """Bad ``--tree`` arg → exit 2, readable error на stderr."""
    result = _run("--tree", "tree_does_not_exist")
    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "tree_does_not_exist" in combined
    assert "not found" in combined.lower() or "error" in combined.lower()


def test_runner_fail_under_triggers_nonzero(tmp_path: Path) -> None:
    """``--fail-under 1.0`` против baseline даёт exit 1 (overall < 1.0)."""
    out = tmp_path / "fail_under.json"
    result = _run("--fail-under", "1.0", "--output", str(out))
    assert result.returncode == 1, (
        f"expected exit 1 from --fail-under 1.0 against baseline, got "
        f"{result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    # Отчёт всё ещё должен быть записан для diagnostics.
    assert out.is_file()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["passed_threshold"] is False


def test_runner_default_fail_under_zero_baseline_passes(tmp_path: Path) -> None:
    """Default ``--fail-under 0.0`` НЕ должен валить baseline (overall ≥ 0)."""
    out = tmp_path / "default.json"
    result = _run("--output", str(out))
    assert result.returncode == 0, (
        f"baseline run unexpectedly failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
