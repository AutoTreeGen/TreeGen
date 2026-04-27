"""DNA matching primitives (Phase 6.1+).

Phase 6.1 поставляет:
    - SharedSegment + find_shared_segments() — half-IBD shared regions
      между двумя DnaTest (см. ADR-0014).
    - predict_relationship() — Phase 6.1 Task 4.
"""

from __future__ import annotations

from dna_analysis.matching.relationships import (
    RelationshipRange,
    predict_relationship,
)
from dna_analysis.matching.segments import SharedSegment, find_shared_segments

__all__ = [
    "RelationshipRange",
    "SharedSegment",
    "find_shared_segments",
    "predict_relationship",
]
