"""Matcher service — обёртка над dna-analysis Phase 6.1 для service-режима.

Принимает два plaintext blob'а (после in-memory decrypt) + путь к
genetic map. Возвращает derived stats без raw genotypes.

Privacy: блобы держатся только в стеке этой функции; никаких записей
на диск, никаких логов с raw values. См. ADR-0020 §«Privacy guards».
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from dna_analysis.errors import UnsupportedFormatError
from dna_analysis.genetic_map import GeneticMap
from dna_analysis.matching import find_shared_segments, predict_relationship
from dna_analysis.matching.segments import DEFAULT_MIN_CM, DEFAULT_MIN_SNPS
from dna_analysis.models import DnaTest
from dna_analysis.parsers import (
    AncestryParser,
    BaseDnaParser,
    TwentyThreeAndMeParser,
)

from dna_service.schemas import MatchRelationship, MatchSegment

_LOG: Final = logging.getLogger(__name__)

_PARSERS: Final[tuple[type[BaseDnaParser], ...]] = (
    TwentyThreeAndMeParser,
    AncestryParser,
)


def parse_blob(blob: bytes) -> DnaTest:
    """Bytes → DnaTest. Бросает UnsupportedFormatError если формат неизвестен."""
    text = blob.decode("utf-8")
    for parser_cls in _PARSERS:
        if parser_cls.detect(text):
            return parser_cls().parse(text)
    msg = "no parser recognised the format of uploaded blob"
    raise UnsupportedFormatError(msg)


def run_match(
    *,
    blob_a: bytes,
    blob_b: bytes,
    genetic_map_dir: Path,
    min_cm: float = DEFAULT_MIN_CM,
    min_snps: int = DEFAULT_MIN_SNPS,
) -> dict[str, object]:
    """Полный matching pipeline для service-уровня.

    Возвращает dict с derived stats для построения MatchResponse:
    `test_a_provider`, `test_b_provider`, `test_a_snp_count`,
    `test_b_snp_count`, `shared_segments`, `total_shared_cm`,
    `longest_segment_cm`, `relationship_predictions`, `warnings`.
    """
    test_a = parse_blob(blob_a)
    test_b = parse_blob(blob_b)
    genetic_map = GeneticMap.from_directory(genetic_map_dir)

    segments = find_shared_segments(test_a, test_b, genetic_map, min_cm=min_cm, min_snps=min_snps)
    total_cm = sum(seg.cm_length for seg in segments)
    longest_cm = max((seg.cm_length for seg in segments), default=0.0)
    relationships = predict_relationship(total_cm, longest_segment_cm=longest_cm)

    _LOG.debug(
        "match completed: %d segments, total %.2f cM, %d candidate relationships",
        len(segments),
        total_cm,
        len(relationships),
    )

    return {
        "test_a_provider": test_a.provider.value,
        "test_b_provider": test_b.provider.value,
        "test_a_snp_count": len(test_a.snps),
        "test_b_snp_count": len(test_b.snps),
        "shared_segments": [
            MatchSegment(
                chromosome=seg.chromosome,
                start_bp=seg.start_bp,
                end_bp=seg.end_bp,
                num_snps=seg.num_snps,
                cm_length=round(seg.cm_length, 3),
            )
            for seg in segments
        ],
        "total_shared_cm": round(total_cm, 2),
        "longest_segment_cm": round(longest_cm, 2),
        "relationship_predictions": [
            MatchRelationship(
                label=r.label,
                probability=round(r.probability, 4),
                cm_range=r.cm_range,
                source=r.source,
            )
            for r in relationships
        ],
        "warnings": _collect_warnings(test_a, test_b, total_cm, segments),
    }


def _collect_warnings(
    test_a: DnaTest,
    test_b: DnaTest,
    total_cm: float,
    segments: Sequence[Any],  # SharedSegment from dna-analysis
) -> list[str]:
    warnings: list[str] = []
    if test_a.provider != test_b.provider:
        warnings.append(
            f"Cross-platform comparison ({test_a.provider.value} vs {test_b.provider.value}): "
            "different chips overlap by ~50-70% rsids; distant relatives may be missed."
        )
    if test_a.reference_build != test_b.reference_build:
        warnings.append(
            f"Reference build mismatch: {test_a.reference_build.value} vs "
            f"{test_b.reference_build.value}. Positions may not align."
        )
    short_segments = sum(1 for s in segments if getattr(s, "cm_length", 0.0) < 15)
    if total_cm > 200 and short_segments >= 5:
        warnings.append(
            "High total cM with many short segments — possible endogamy "
            "(Ashkenazi, Roma, Amish). Total cM may overestimate closeness ~1.5-2x."
        )
    return warnings
