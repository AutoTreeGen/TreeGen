"""dna-analysis — pure-function парсеры и алгоритмы для DNA-данных.

См. ADR-0012 (privacy & architecture). Пакет НЕ хранит данные, НЕ
делает сетевых вызовов, НЕ управляет ключами шифрования. Storage +
HTTP — `services/dna-service/` (Phase 6.1).
"""

from __future__ import annotations

from dna_analysis.clustering import (
    Cluster,
    ClusterEdge,
    ClusteringAlgorithm,
    ClusteringResult,
    ClusterMatch,
    EndogamyAssessment,
    build_co_match_graph,
    detect_endogamy,
    run_clustering,
)
from dna_analysis.dispatcher import parse_raw
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
    Sex,
    Snp,
)
from dna_analysis.sex import infer_sex
from dna_analysis.triangulation import (
    Match,
    TriangulationGroup,
    TriangulationSegment,
    bayes_boost,
    find_triangulation_groups,
)

# Phase 16.1: alias для downstream consumers (Phase 16.2 dna-painter и далее),
# которым семантически удобнее `ParseResult` для возврата `parse_raw`.
# Тип идентичен DnaTest — back-compat без breaking change.
ParseResult = DnaTest

__all__ = [
    "Chromosome",
    "Cluster",
    "ClusterEdge",
    "ClusterMatch",
    "ClusteringAlgorithm",
    "ClusteringResult",
    "DnaParseError",
    "DnaTest",
    "EndogamyAssessment",
    "GeneticMap",
    "GeneticMapError",
    "Genotype",
    "Match",
    "ParseResult",
    "Provider",
    "ReferenceBuild",
    "RelationshipRange",
    "Sex",
    "SharedSegment",
    "Snp",
    "TriangulationGroup",
    "TriangulationSegment",
    "UnsupportedFormatError",
    "bayes_boost",
    "build_co_match_graph",
    "detect_endogamy",
    "find_shared_segments",
    "find_triangulation_groups",
    "infer_sex",
    "parse_raw",
    "predict_relationship",
    "run_clustering",
]
