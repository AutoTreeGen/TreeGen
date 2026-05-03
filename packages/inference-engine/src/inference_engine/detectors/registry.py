"""Registry for Phase 26.x tree-level detectors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from inference_engine.detectors import (
    cross_platform_dna_match,
    dna_vs_tree,
    gedcom_safe_merge,
    historical_place_jurisdiction,
    metric_book_ocr,
    revision_list_household,
)
from inference_engine.detectors.result import DetectorResult

DetectorFn = Callable[[dict[str, Any]], DetectorResult]


_DETECTORS: list[DetectorFn] = [
    cross_platform_dna_match.detect,
    dna_vs_tree.detect,
    gedcom_safe_merge.detect,
    metric_book_ocr.detect,
    revision_list_household.detect,
    historical_place_jurisdiction.detect,
]


def all_detectors() -> list[DetectorFn]:
    """Return a defensive copy of the registered detector list."""
    return list(_DETECTORS)


def merge_into(target: DetectorResult, other: DetectorResult) -> None:
    """Merge another detector result into the target result in place."""
    target.engine_flags.extend(other.engine_flags)
    target.relationship_claims.extend(other.relationship_claims)
    target.merge_decisions.extend(other.merge_decisions)
    target.place_corrections.extend(other.place_corrections)
    target.quarantined_claims.extend(other.quarantined_claims)
    target.sealed_set_candidates.extend(other.sealed_set_candidates)
    target.evaluation_results.update(other.evaluation_results)


def run_all(tree: dict[str, Any]) -> DetectorResult:
    """Run all registered detectors and return one aggregated result."""
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
