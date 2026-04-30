"""Leiden community detection с NetworkX-greedy fallback (Phase 6.7a / ADR-0063).

Workflow:

1. Caller получает граф через :func:`dna_analysis.clustering.graph.build_co_match_graph`.
2. :func:`run_clustering` пытается импортировать ``leidenalg`` + ``igraph``.
3. Если оба импортнулись (production path): запускает Leiden с
   RBConfigurationVertexPartition и параметром ``resolution``.
4. Если ImportError (fallback path, обычно Windows + Python 3.13 без
   pre-built C-extension wheels): WARNING-логом сообщает о fallback'е и
   запускает ``networkx.algorithms.community.greedy_modularity_communities``
   с тем же графом.

Оба алгоритма возвращают **partition'ы** — non-overlapping communities
nodes. Отдельные узлы (без рёбер) формируют каждый свой кластер размера 1
по convention'у; caller обычно фильтрует ``len(members) >= 2`` перед
персистом, но :func:`run_clustering` возвращает всё, чтобы поведение
было detectable в тестах.

См. ADR-0063 §«Decision: Leiden over Louvain» — Leiden гарантирует
connected communities и не страдает от bad partitioning, которому
подвержен Louvain (Traag, Waltman, Van Eck 2019).
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from dna_analysis.clustering.graph import ClusterEdge

_LOG: Final = logging.getLogger(__name__)


def _detect_leiden_available() -> bool:
    # PLC0415: imports внутри функции — намеренные. Весь смысл этого
    # модуля — gracefully fall back, если C-extensions не собрались.
    try:
        import igraph  # noqa: F401, PLC0415
        import leidenalg  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


# Module-level флаг — вычисляется один раз на import. Тесты могут
# подменять через monkeypatch для проверки fallback-пути.
LEIDEN_AVAILABLE: bool = _detect_leiden_available()

DEFAULT_RESOLUTION: Final[float] = 1.0


class ClusteringAlgorithm(StrEnum):
    """Какой алгоритм фактически использовался для cluster run'а.

    Сохраняется в ``DnaCluster.algorithm`` для reproducibility и для
    UI-warning'а («ваш кластеринг прогнан в degraded-режиме, поставьте
    leidenalg для лучшего результата»).
    """

    LEIDEN = "leiden"
    NETWORKX_GREEDY = "networkx_greedy"


class Cluster(BaseModel):
    """Один cluster на выходе.

    ``members`` — отсортированный кортеж match_id (стабильно для тестов).
    ``avg_internal_weight`` — средний вес рёбер внутри cluster'а;
    индикатор когезии, используется в endogamy detection и UI ranking.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    members: tuple[str, ...] = Field(..., min_length=1)
    avg_internal_weight: float = Field(default=0.0, ge=0.0)


class ClusteringResult(BaseModel):
    """Результат одного clustering run'а.

    ``algorithm`` — фактически использованный алгоритм. Caller пишет в
    ``DnaCluster.algorithm`` без перевычисления.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    algorithm: ClusteringAlgorithm
    clusters: tuple[Cluster, ...]
    parameters: dict[str, Any] = Field(default_factory=dict)


def _avg_internal_weight(members: set[str], edges: list[ClusterEdge]) -> float:
    """Среднее значение weight по рёбрам, оба конца которых внутри ``members``."""
    inside = [e.weight for e in edges if e.source in members and e.target in members]
    if not inside:
        return 0.0
    return sum(inside) / len(inside)


def _run_leiden(
    nodes: list[str],
    edges: list[ClusterEdge],
    resolution: float,
) -> list[set[str]]:
    """Leiden via leidenalg + igraph. Caller'ом проверено LEIDEN_AVAILABLE."""
    # PLC0415: deferred import; модуль может отсутствовать в env.
    import igraph  # noqa: PLC0415
    import leidenalg  # noqa: PLC0415

    g = igraph.Graph()
    g.add_vertices(nodes)
    if edges:
        g.add_edges([(e.source, e.target) for e in edges])
        g.es["weight"] = [e.weight for e in edges]
    partition = leidenalg.find_partition(
        g,
        leidenalg.RBConfigurationVertexPartition,
        weights="weight" if edges else None,
        resolution_parameter=resolution,
        seed=42,  # детерминизм для тестов и reproducibility cluster run'ов
    )
    communities: list[set[str]] = []
    for community in partition:
        communities.append({nodes[i] for i in community})
    return communities


def _run_networkx_greedy(
    nodes: list[str],
    edges: list[ClusterEdge],
    resolution: float,
) -> list[set[str]]:
    """Fallback: NetworkX greedy modularity communities."""
    # PLC0415: ленивый import симметрично с _run_leiden; модули обычно
    # есть, но локальный импорт держит symmetry с Leiden-веткой и не
    # тянет networkx, если caller вообще не делает clustering'а.
    import networkx as nx  # noqa: PLC0415
    from networkx.algorithms.community import greedy_modularity_communities  # noqa: PLC0415

    g = nx.Graph()
    g.add_nodes_from(nodes)
    for e in edges:
        g.add_edge(e.source, e.target, weight=e.weight)
    if g.number_of_edges() == 0:
        # Greedy modularity requires at least one edge; на пустом графе
        # отдаём каждому узлу собственный community.
        return [{n} for n in nodes]
    communities = greedy_modularity_communities(
        g,
        weight="weight",
        resolution=resolution,
    )
    return [set(c) for c in communities]


def run_clustering(
    nodes: list[str],
    edges: list[ClusterEdge],
    *,
    resolution: float = DEFAULT_RESOLUTION,
    force_algorithm: ClusteringAlgorithm | None = None,
) -> ClusteringResult:
    """Run community detection on a co-match graph.

    Args:
        nodes: Список match_id (узлы графа). Может быть пустым.
        edges: Список :class:`ClusterEdge`. Может быть пустым; на
            edge-free графе каждый узел становится singleton-cluster'ом.
        resolution: Resolution-параметр для modularity-based методов.
            > 1.0 → больше мелких сообществ; < 1.0 → меньше крупных.
            Default 1.0 — стандартный modularity-режим.
        force_algorithm: Явно выбрать алгоритм вместо auto-detection.
            Используется в тестах для проверки fallback-пути; в проде
            оставлять ``None``.

    Returns:
        :class:`ClusteringResult` с фактически использованным алгоритмом
        и отсортированным списком cluster'ов (по убыванию размера, потом
        лексикографически по первому member'у — стабильность для тестов).
    """
    if resolution <= 0:
        msg = "resolution must be positive"
        raise ValueError(msg)
    if not nodes:
        return ClusteringResult(
            algorithm=force_algorithm or ClusteringAlgorithm.LEIDEN,
            clusters=(),
            parameters={"resolution": resolution},
        )

    use_leiden: bool
    if force_algorithm is ClusteringAlgorithm.LEIDEN:
        if not LEIDEN_AVAILABLE:
            msg = (
                "force_algorithm=LEIDEN requested but leidenalg/igraph "
                "are not importable in this environment"
            )
            raise RuntimeError(msg)
        use_leiden = True
    elif force_algorithm is ClusteringAlgorithm.NETWORKX_GREEDY:
        use_leiden = False
    else:
        use_leiden = LEIDEN_AVAILABLE
        if not use_leiden:
            _LOG.warning(
                "leidenalg not installed (likely uv on Windows / Python 3.13 "
                "without C-extension wheels). Falling back to NetworkX greedy "
                "modularity. Install leidenalg + igraph for better cluster "
                "stability (see ADR-0063)."
            )

    communities = (
        _run_leiden(nodes, edges, resolution)
        if use_leiden
        else _run_networkx_greedy(nodes, edges, resolution)
    )

    # Стабильная сортировка: сначала по убыванию размера, потом по
    # лексикографически минимальному member'у.
    sorted_comms = sorted(communities, key=lambda c: (-len(c), min(c) if c else ""))
    clusters: list[Cluster] = []
    for community in sorted_comms:
        members = tuple(sorted(community))
        clusters.append(
            Cluster(
                members=members,
                avg_internal_weight=_avg_internal_weight(community, edges),
            )
        )

    return ClusteringResult(
        algorithm=ClusteringAlgorithm.LEIDEN if use_leiden else ClusteringAlgorithm.NETWORKX_GREEDY,
        clusters=tuple(clusters),
        parameters={"resolution": resolution},
    )
