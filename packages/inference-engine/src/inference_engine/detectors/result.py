"""Shared result type for Phase 26.x tree-level detectors.

Detector modules return DetectorResult objects. The registry aggregates them
into the final EngineOutput payload used by scripts/run_eval.py.

DetectorResult intentionally mirrors the list-like fields in EngineOutput plus
evaluation_results. This keeps detector output deterministic and easy to merge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DetectorResult:
    """Output produced by one tree-level detector."""

    engine_flags: list[str] = field(default_factory=list)
    relationship_claims: list[dict[str, Any]] = field(default_factory=list)
    merge_decisions: list[dict[str, Any]] = field(default_factory=list)
    place_corrections: list[dict[str, Any]] = field(default_factory=list)
    quarantined_claims: list[dict[str, Any]] = field(default_factory=list)
    sealed_set_candidates: list[dict[str, Any]] = field(default_factory=list)
    evaluation_results: dict[str, bool] = field(default_factory=dict)


__all__ = ["DetectorResult"]
