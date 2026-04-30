"""dna-analysis — pure-function парсеры и алгоритмы для DNA-данных.

См. ADR-0012 (privacy & architecture). Пакет НЕ хранит данные, НЕ
делает сетевых вызовов, НЕ управляет ключами шифрования. Storage +
HTTP — `services/dna-service/` (Phase 6.1).
"""

from __future__ import annotations

from dna_analysis.errors import DnaParseError, UnsupportedFormatError
from dna_analysis.genetic_map import GeneticMap, GeneticMapError
from dna_analysis.matching import (
    RelationshipRange,
    SharedSegment,
    find_shared_segments,
    predict_relationship,
)
from dna_analysis.models import (
    Chromosome,
    DnaTest,
    Genotype,
    Provider,
    ReferenceBuild,
    Snp,
)
from dna_analysis.triangulation import (
    Match,
    TriangulationGroup,
    TriangulationSegment,
    bayes_boost,
    find_triangulation_groups,
)

__all__ = [
    "Chromosome",
    "DnaParseError",
    "DnaTest",
    "GeneticMap",
    "GeneticMapError",
    "Genotype",
    "Match",
    "Provider",
    "ReferenceBuild",
    "RelationshipRange",
    "SharedSegment",
    "Snp",
    "TriangulationGroup",
    "TriangulationSegment",
    "UnsupportedFormatError",
    "bayes_boost",
    "find_shared_segments",
    "find_triangulation_groups",
    "predict_relationship",
]
