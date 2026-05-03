"""Phase 26.2 — registry для tree-level detectors.

Каждый detector — pure function ``detect(tree: dict) -> DetectorResult``.
``engine.run_tree`` итерирует зарегистрированные детекторы и мерджит
``DetectorResult`` в финальный ``EngineOutput``.

Phase 26.2 ставит один детектор:

- ``dna_vs_tree.detect`` (tree_11 — DNA-vs-tree parentage contradiction).

Phase 26.3+ добавит остальные (gedcom_safe_merge, metric_book_ocr_correction,
revision_list_household, immigration_name_change, …) — список см.
``inference_engine.detectors.__init__``.

Контракт detector'а:

- **Pure function.** Никакого I/O / стохастики / LLM-вызовов. Один и
  тот же tree-input → один и тот же ``DetectorResult``.
- **Ничего не читает из answer key.** Не смотрит на
  ``expected_engine_flags`` или ``expected_confidence_outputs``;
  только на input-evidence (``input_dna_matches``,
  ``input_user_assertions``, ``input_archive_snippets``,
  ``input_gedcom_excerpt``).
- **Эмитит только то, что подтверждает evidence.** Если детектор не
  уверен — он молчит, а не помечает ``evaluation_results=True`` ради
  flag-score.

Merge-семантика — list-extend для всех list-полей, ``dict.update``
для ``evaluation_results`` (последний detector выигрывает на
конфликте assertion_id; в Phase 26.2 один detector → no conflicts).

``DetectorResult`` живёт в отдельном модуле ``detectors.result``,
чтобы детекторы могли импортить его без циркулярной зависимости с
этим registry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from inference_engine.detectors import dna_vs_tree
from inference_engine.detectors.result import DetectorResult

DetectorFn = Callable[[dict[str, Any]], DetectorResult]


_DETECTORS: list[DetectorFn] = [
    dna_vs_tree.detect,
]


def all_detectors() -> list[DetectorFn]:
    """Снапшот списка зарегистрированных детекторов (defensive copy)."""
    return list(_DETECTORS)


def merge_into(target: DetectorResult, other: DetectorResult) -> None:
    """In-place merge ``other`` в ``target``.

    List-поля extend'ятся в порядке добавления; ``evaluation_results``
    обновляется через ``dict.update`` (последний writer выигрывает).
    """
    target.engine_flags.extend(other.engine_flags)
    target.relationship_claims.extend(other.relationship_claims)
    target.merge_decisions.extend(other.merge_decisions)
    target.place_corrections.extend(other.place_corrections)
    target.quarantined_claims.extend(other.quarantined_claims)
    target.sealed_set_candidates.extend(other.sealed_set_candidates)
    target.evaluation_results.update(other.evaluation_results)


def run_all(tree: dict[str, Any]) -> DetectorResult:
    """Прогнать все зарегистрированные детекторы и вернуть aggregated результат."""
    aggregated = DetectorResult()
    for fn in _DETECTORS:
        merge_into(aggregated, fn(tree))
    return aggregated


__all__ = [
    "DetectorFn",
    "DetectorResult",
    "all_detectors",
    "merge_into",
    "run_all",
]
