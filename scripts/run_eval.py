"""Phase 26.1 — evaluation harness runner.

Запуск:

    uv run python scripts/run_eval.py                    # все 20 деревьев
    uv run python scripts/run_eval.py --tree tree_11_unknown_father_npe_dna_contradiction
    uv run python scripts/run_eval.py --fail-under 0.5

Что делает:

1. Сканирует ``data/test_corpus/trees/tree_*.json`` (или один tree, если
   ``--tree``).
2. Загружает ``data/test_corpus/harness/autotreegen_evaluation_harness_trees1_20.json``.
3. Для каждого tree вызывает ``inference_engine.engine.run_tree(tree)``.
4. Валидирует output schema (см. ADR-0084 §"Output contract").
5. Сравнивает ``expected_engine_flags`` (из tree-fixture'а) с
   ``engine_flags`` (из engine output) — flag_score.
6. Сравнивает ``evaluation_assertions`` через ``evaluation_results`` —
   assertion_score.
7. Берёт ``schema_integrity.required_keys_present`` из harness — schema_score.
8. Печатает per-tree score и overall score.
9. Пишет JSON-отчёт в ``reports/eval/autotreegen_eval_report.json`` (или в
   ``--output``).

Score formula (см. ADR-0084 §"Scoring"):

    score = 0.7 * assertion_score + 0.2 * flag_score + 0.1 * schema_score

Phase 26.1 baseline возвращает пустой ``engine_flags`` и all-False
``evaluation_results`` — итоговый overall score близок к 0. Это ожидаемо;
``--fail-under`` по умолчанию 0.0, так что runner не падает на baseline.
Phase 26.2+ детекторы будут поднимать score tree by tree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_ROOT = REPO_ROOT / "data" / "test_corpus"
TREES_DIR = CORPUS_ROOT / "trees"
HARNESS_FILE = CORPUS_ROOT / "harness" / "autotreegen_evaluation_harness_trees1_20.json"
DEFAULT_REPORT_PATH = REPO_ROOT / "reports" / "eval" / "autotreegen_eval_report.json"

# Делает скрипт запускаемым stand-alone (``python scripts/run_eval.py``)
# даже если workspace ещё не установлен в .venv: ``mypy_path`` в pyproject
# уже знает про этот src dir, но runtime — нет. ``uv run`` через workspace
# тоже работает (пакет резолвится напрямую).
_INFERENCE_SRC = REPO_ROOT / "packages" / "inference-engine" / "src"
if _INFERENCE_SRC.exists() and str(_INFERENCE_SRC) not in sys.path:
    sys.path.insert(0, str(_INFERENCE_SRC))

from inference_engine.engine import run_tree  # noqa: E402
from inference_engine.output_schema import validate_output  # noqa: E402


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_trees(trees_dir: Path = TREES_DIR) -> dict[str, Path]:
    """Просканировать ``trees_dir/tree_*.json``, вернуть map ``tree_id`` → path.

    Raises:
        FileNotFoundError: Если ``trees_dir`` отсутствует.
        ValueError: Если tree-файл не содержит ``tree_id`` или дублирует id.
    """
    if not trees_dir.is_dir():
        msg = f"test corpus trees dir not found: {trees_dir}"
        raise FileNotFoundError(msg)

    out: dict[str, Path] = {}
    for path in sorted(trees_dir.glob("tree_*.json")):
        data = _load_json(path)
        tid = data.get("tree_id")
        if not isinstance(tid, str) or not tid:
            msg = f"{path.name}: missing or invalid 'tree_id' field"
            raise ValueError(msg)
        if tid in out:
            msg = f"duplicate tree_id '{tid}' in {path.name} and {out[tid].name}"
            raise ValueError(msg)
        out[tid] = path
    return out


def load_harness(harness_path: Path = HARNESS_FILE) -> dict[str, Any]:
    """Загрузить harness JSON. ``FileNotFoundError`` если нет файла."""
    if not harness_path.is_file():
        msg = f"harness file not found: {harness_path}"
        raise FileNotFoundError(msg)
    data = _load_json(harness_path)
    if not isinstance(data, dict):
        msg = f"{harness_path.name}: top-level must be a JSON object"
        raise ValueError(msg)
    return data


def evaluate_tree(
    tree: dict[str, Any],
    engine_output: dict[str, Any],
    harness_case: dict[str, Any] | None,
) -> dict[str, Any]:
    """Сравнить engine output с tree-fixture expectations + harness metadata.

    Args:
        tree: Loaded tree JSON.
        engine_output: Output of ``run_tree(tree)``.
        harness_case: Соответствующий test case из harness JSON, или ``None``
            если в harness нет такого tree_id (в таком случае schema_score=1.0).

    Returns:
        Dict с per-tree метриками: ``score``, ``assertion_score``,
        ``flag_score``, ``schema_score``, ``flag_hits``, ``flag_misses``,
        ``assertions_total``, ``assertions_passed``.
    """
    expected_flags = set(tree.get("expected_engine_flags") or [])
    actual_flags = set(engine_output.get("engine_flags") or [])
    flag_hits = sorted(expected_flags & actual_flags)
    flag_misses = sorted(expected_flags - actual_flags)

    raw_assertions = tree.get("evaluation_assertions") or []
    eval_results = engine_output.get("evaluation_results") or {}
    tree_num = _parse_tree_number(tree.get("tree_id", ""))

    assertions_total = 0
    assertions_passed = 0
    for idx, item in enumerate(raw_assertions):
        if not isinstance(item, dict):
            continue
        assertions_total += 1
        aid_raw = item.get("assertion_id")
        aid = (
            aid_raw
            if isinstance(aid_raw, str) and aid_raw
            else f"eval_{tree_num:02d}_{idx + 1:03d}"
        )
        if eval_results.get(aid) is True:
            assertions_passed += 1

    a_score = assertions_passed / assertions_total if assertions_total else 1.0
    f_score = len(flag_hits) / len(expected_flags) if expected_flags else 1.0

    if harness_case is not None:
        schema_integrity = harness_case.get("schema_integrity") or {}
        s_score = 1.0 if schema_integrity.get("required_keys_present") else 0.0
    else:
        s_score = 1.0

    score = 0.7 * a_score + 0.2 * f_score + 0.1 * s_score

    return {
        "tree_id": tree.get("tree_id"),
        "score": round(score, 4),
        "assertion_score": round(a_score, 4),
        "flag_score": round(f_score, 4),
        "schema_score": s_score,
        "flag_hits": flag_hits,
        "flag_misses": flag_misses,
        "assertions_total": assertions_total,
        "assertions_passed": assertions_passed,
    }


def _parse_tree_number(tree_id: str) -> int:
    parts = tree_id.split("_")
    return int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Возвращает exit code (0 = OK, 1 = fail-under, 2 = bad input)."""
    parser = argparse.ArgumentParser(
        description="Phase 26.1 evaluation harness runner",
    )
    parser.add_argument(
        "--tree",
        help="Run only this tree_id (e.g. tree_11_unknown_father_npe_dna_contradiction).",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help=(
            "Exit non-zero if overall score is below this threshold. "
            "Default 0.0 — baseline (Phase 26.1) is expected to be near zero."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"Path for JSON report. Default: {DEFAULT_REPORT_PATH.relative_to(REPO_ROOT)}",
    )
    args = parser.parse_args(argv)

    try:
        tree_paths = discover_trees()
        harness = load_harness()
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cases_by_id: dict[str, dict[str, Any]] = {
        tc["tree_id"]: tc
        for tc in harness.get("test_cases", []) or []
        if isinstance(tc, dict) and isinstance(tc.get("tree_id"), str)
    }

    if args.tree:
        if args.tree not in tree_paths:
            available = ", ".join(sorted(tree_paths)) or "(none)"
            print(
                f"error: tree_id '{args.tree}' not found in {TREES_DIR}.\navailable: {available}",
                file=sys.stderr,
            )
            return 2
        targets = {args.tree: tree_paths[args.tree]}
    else:
        targets = tree_paths

    results: list[dict[str, Any]] = []
    for tid, path in targets.items():
        tree = _load_json(path)
        engine_output = run_tree(tree)
        # ADR-0084: каждый run_tree output должен пройти Pydantic-валидацию.
        # Если детектор когда-нибудь сломает схему — runner упадёт здесь,
        # а не молча продолжит со скоринг-stale-данными.
        validate_output(engine_output)
        result = evaluate_tree(tree, engine_output, cases_by_id.get(tid))
        results.append(result)
        print(
            f"  {tid}: {result['score']:.4f}  "
            f"(assert={result['assertion_score']:.2f}  "
            f"flag={result['flag_score']:.2f}  "
            f"schema={result['schema_score']:.0f}; "
            f"{result['assertions_passed']}/{result['assertions_total']} assertions, "
            f"{len(result['flag_hits'])}/{len(result['flag_hits']) + len(result['flag_misses'])} flags)"
        )

    overall = sum(r["score"] for r in results) / len(results) if results else 0.0
    print(f"\noverall: {overall:.4f}  ({len(results)} tree{'s' if len(results) != 1 else ''})")

    report = {
        "harness_id": harness.get("harness_id"),
        "harness_version": harness.get("version"),
        "trees_evaluated": len(results),
        "overall_score": round(overall, 4),
        "fail_under": args.fail_under,
        "passed_threshold": overall >= args.fail_under,
        "tree_results": results,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"report written to: {args.output}")

    if overall < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
