"""DNA AutoClusters — graph-based community detection for match-lists.

Phase 6.7 split:

* **6.7a (этот PR)** — graph build, Leiden clustering (с NetworkX-greedy
  fallback), endogamy detection.
* **6.7b** — pile-up region detection (segment overlap analysis).
* **6.7c** — AI labels через ai-layer use_case'ы.

Pure-functions, см. ADR-0012: ничего не пишет в БД и не делает сетевых
вызовов. Caller (dna-service) сам резолвит ORM → :class:`ClusterMatch`,
запускает алгоритм, сохраняет результаты.

См. ADR-0063 §«Decision» для обоснования Leiden vs Louvain и роли
NetworkX-greedy как acceptable fallback'а для environment'ов, где
``leidenalg`` C-extensions не собираются (в т.ч. Windows + Python 3.13
edge-cases).
"""

from __future__ import annotations

from dna_analysis.clustering.endogamy import (
    DEFAULT_ENDOGAMY_CM_THRESHOLD,
    DEFAULT_MIN_PAIRWISE_FOR_ENDOGAMY,
    POPULATION_THRESHOLDS_CM,
    EndogamyAssessment,
    detect_endogamy,
)
from dna_analysis.clustering.graph import (
    DEFAULT_MIN_SHARED_CM,
    ClusterEdge,
    ClusterMatch,
    build_co_match_graph,
)
from dna_analysis.clustering.leiden import (
    LEIDEN_AVAILABLE,
    Cluster,
    ClusteringAlgorithm,
    ClusteringResult,
    run_clustering,
)

__all__ = [
    "DEFAULT_ENDOGAMY_CM_THRESHOLD",
    "DEFAULT_MIN_PAIRWISE_FOR_ENDOGAMY",
    "DEFAULT_MIN_SHARED_CM",
    "LEIDEN_AVAILABLE",
    "POPULATION_THRESHOLDS_CM",
    "Cluster",
    "ClusterEdge",
    "ClusterMatch",
    "ClusteringAlgorithm",
    "ClusteringResult",
    "EndogamyAssessment",
    "build_co_match_graph",
    "detect_endogamy",
    "run_clustering",
]
