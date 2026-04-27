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

__all__ = [
    "Chromosome",
    "DnaParseError",
    "DnaTest",
    "GeneticMap",
    "GeneticMapError",
    "Genotype",
    "Provider",
    "ReferenceBuild",
    "RelationshipRange",
    "SharedSegment",
    "Snp",
    "UnsupportedFormatError",
    "find_shared_segments",
    "predict_relationship",
]
