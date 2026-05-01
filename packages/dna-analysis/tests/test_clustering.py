"""Tests for clustering / endogamy (Phase 6.7a).

Coverage:

* graph build — числовые pairwise + binary fallback + threshold filter +
  symmetry + cross-shard non-existent match references.
* Leiden — synthetic 3-cluster graph → находит 3 community, deterministic
  via fixed seed.
* NetworkX-greedy fallback — same graph, force fallback path → находит
  partition (точное число community может варьироваться, проверяем что
  все nodes покрыты и все три synthetic-clique distinguishable).
* Endogamy — AJ-shaped synthetic data (high avg cM) → flagged + 'AJ' label.
* Endogamy — generic кластер (low cM) → not flagged.
* Endogamy — degraded mode (только binary edges, нет cM) → warning=False
  по convention'у.
"""

from __future__ import annotations

import pytest
from dna_analysis.clustering import (
    LEIDEN_AVAILABLE,
    POPULATION_THRESHOLDS_CM,
    ClusterEdge,
    ClusteringAlgorithm,
    ClusterMatch,
    build_co_match_graph,
    detect_endogamy,
    run_clustering,
)

# ---------------------------------------------------------------------------
# graph build
# ---------------------------------------------------------------------------


def test_build_graph_with_numeric_pairwise() -> None:
    matches = [
        ClusterMatch(match_id="A", pairwise_cm={"B": 50.0, "C": 5.0}),
        ClusterMatch(match_id="B", pairwise_cm={"A": 50.0}),
        ClusterMatch(match_id="C", pairwise_cm={"A": 5.0}),
    ]
    nodes, edges = build_co_match_graph(matches, min_shared_cm=8.0)
    assert nodes == ["A", "B", "C"]
    # A-C под порогом (5 < 8) → отброшено. A-B остаётся.
    assert len(edges) == 1
    assert edges[0].source == "A"
    assert edges[0].target == "B"
    assert edges[0].weight == pytest.approx(50.0)


def test_build_graph_canonical_orientation() -> None:
    """Каждое ребро отдаётся как (source < target) лексикографически, без дублей."""
    matches = [
        ClusterMatch(match_id="zeta", pairwise_cm={"alpha": 30.0}),
        ClusterMatch(match_id="alpha", pairwise_cm={"zeta": 30.0}),
    ]
    _, edges = build_co_match_graph(matches, min_shared_cm=10.0)
    assert len(edges) == 1
    assert edges[0].source == "alpha"
    assert edges[0].target == "zeta"


def test_build_graph_takes_max_when_pairwise_asymmetric() -> None:
    """Если caller дал A→B=20, B→A=25, берём max (избегаем зависимости от направления)."""
    matches = [
        ClusterMatch(match_id="A", pairwise_cm={"B": 20.0}),
        ClusterMatch(match_id="B", pairwise_cm={"A": 25.0}),
    ]
    _, edges = build_co_match_graph(matches, min_shared_cm=10.0)
    assert edges[0].weight == pytest.approx(25.0)


def test_build_graph_binary_fallback_for_ancestry_style() -> None:
    """Когда у нас только membership (Ancestry-style), ребро с weight=1.0."""
    matches = [
        ClusterMatch(match_id="A", shared_match_ids=frozenset({"B", "C"})),
        ClusterMatch(match_id="B", shared_match_ids=frozenset({"A"})),
        ClusterMatch(match_id="C", shared_match_ids=frozenset({"A"})),
    ]
    _, edges = build_co_match_graph(matches)
    assert len(edges) == 2
    assert all(e.weight == 1.0 for e in edges)


def test_build_graph_numeric_takes_priority_over_binary() -> None:
    """Если есть и pairwise_cm и shared_match_ids на ту же пару — берём cM."""
    matches = [
        ClusterMatch(
            match_id="A",
            pairwise_cm={"B": 40.0},
            shared_match_ids=frozenset({"B"}),
        ),
        ClusterMatch(match_id="B", pairwise_cm={"A": 40.0}),
    ]
    _, edges = build_co_match_graph(matches, min_shared_cm=10.0)
    assert len(edges) == 1
    assert edges[0].weight == pytest.approx(40.0)


def test_build_graph_filters_unknown_neighbours() -> None:
    """match'ы, на которых нас ссылаются, но которых нет в input'е, тихо отбрасываются."""
    matches = [
        ClusterMatch(match_id="A", pairwise_cm={"GHOST": 50.0, "B": 30.0}),
        ClusterMatch(match_id="B", pairwise_cm={"A": 30.0}),
    ]
    _, edges = build_co_match_graph(matches, min_shared_cm=10.0)
    assert len(edges) == 1
    assert {edges[0].source, edges[0].target} == {"A", "B"}


def test_build_graph_rejects_self_loop_via_pydantic() -> None:
    with pytest.raises(ValueError, match="self-loop"):
        ClusterMatch(match_id="A", pairwise_cm={"A": 10.0})


def test_build_graph_rejects_duplicate_match_id() -> None:
    matches = [
        ClusterMatch(match_id="A"),
        ClusterMatch(match_id="A"),
    ]
    with pytest.raises(ValueError, match="duplicate match_id"):
        build_co_match_graph(matches)


# ---------------------------------------------------------------------------
# Clustering — synthetic 3-clique graph
# ---------------------------------------------------------------------------


def _three_clique_graph() -> tuple[list[str], list[ClusterEdge]]:
    """3 disjoint plump 4-node cliques (high internal weight), no cross-edges.

    Любой вменяемый modularity-based clusterer должен найти ровно три community.
    """
    cliques = [
        ("A1", "A2", "A3", "A4"),
        ("B1", "B2", "B3", "B4"),
        ("C1", "C2", "C3", "C4"),
    ]
    nodes: list[str] = []
    edges: list[ClusterEdge] = []
    for clique in cliques:
        for n in clique:
            nodes.append(n)
        for i, a in enumerate(clique):
            for b in clique[i + 1 :]:
                src, tgt = (a, b) if a < b else (b, a)
                edges.append(ClusterEdge(source=src, target=tgt, weight=50.0))
    nodes.sort()
    edges.sort(key=lambda e: (e.source, e.target))
    return nodes, edges


@pytest.mark.skipif(not LEIDEN_AVAILABLE, reason="leidenalg/igraph not installed in this env")
def test_leiden_finds_three_disjoint_cliques() -> None:
    nodes, edges = _three_clique_graph()
    result = run_clustering(
        nodes,
        edges,
        force_algorithm=ClusteringAlgorithm.LEIDEN,
    )
    assert result.algorithm is ClusteringAlgorithm.LEIDEN
    assert len(result.clusters) == 3
    # Каждый clique — собственный cluster, mapping prefix → members.
    for cluster in result.clusters:
        prefixes = {m[0] for m in cluster.members}
        assert len(prefixes) == 1, f"cluster mixes prefixes: {cluster.members}"
        assert len(cluster.members) == 4


def test_networkx_greedy_fallback_partitions_three_cliques() -> None:
    """Force fallback path; точное число community может зависеть от
    реализации (greedy modularity), но nodes должны распределиться так,
    чтобы каждый prefix был внутри одного community."""
    nodes, edges = _three_clique_graph()
    result = run_clustering(
        nodes,
        edges,
        force_algorithm=ClusteringAlgorithm.NETWORKX_GREEDY,
    )
    assert result.algorithm is ClusteringAlgorithm.NETWORKX_GREEDY
    # Все nodes покрыты ровно один раз.
    seen: set[str] = set()
    for cluster in result.clusters:
        seen.update(cluster.members)
    assert seen == set(nodes)
    # Никакой cluster не смешивает prefixes — три clique'и должны
    # остаться раздельными.
    for cluster in result.clusters:
        prefixes = {m[0] for m in cluster.members}
        assert len(prefixes) == 1, f"cluster mixes prefixes: {cluster.members}"


def test_run_clustering_handles_empty_input() -> None:
    result = run_clustering([], [])
    assert result.clusters == ()


def test_run_clustering_force_leiden_raises_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если caller просит Leiden, но окружение его не имеет — explicit RuntimeError."""
    from dna_analysis.clustering import leiden as leiden_mod

    monkeypatch.setattr(leiden_mod, "LEIDEN_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="leidenalg/igraph"):
        run_clustering(
            ["A", "B"],
            [ClusterEdge(source="A", target="B", weight=10.0)],
            force_algorithm=ClusteringAlgorithm.LEIDEN,
        )


def test_run_clustering_auto_falls_back_to_networkx_greedy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Без force_algorithm и LEIDEN_AVAILABLE=False — graceful fallback + WARNING."""
    from dna_analysis.clustering import leiden as leiden_mod

    monkeypatch.setattr(leiden_mod, "LEIDEN_AVAILABLE", False)
    nodes, edges = _three_clique_graph()
    with caplog.at_level("WARNING", logger="dna_analysis.clustering.leiden"):
        result = run_clustering(nodes, edges)
    assert result.algorithm is ClusteringAlgorithm.NETWORKX_GREEDY
    assert any("leidenalg not installed" in rec.message for rec in caplog.records)
    assert len(result.clusters) >= 1


# ---------------------------------------------------------------------------
# Endogamy detection
# ---------------------------------------------------------------------------


def test_population_thresholds_are_descending() -> None:
    """AJ-первый, чтобы _classify_population подбирал самый специфичный label."""
    thresholds = [t for _, t in POPULATION_THRESHOLDS_CM]
    assert thresholds == sorted(thresholds, reverse=True)


def test_endogamy_aj_shaped_cluster_flagged() -> None:
    """4-человечный кластер с avg pairwise cM = 35 (выше AJ=30) → flag + 'AJ'."""
    members = [ClusterMatch(match_id=f"M{i}", segments_with_owner=8) for i in range(4)]
    edges = [
        ClusterEdge(source="M0", target="M1", weight=35.0),
        ClusterEdge(source="M0", target="M2", weight=33.0),
        ClusterEdge(source="M0", target="M3", weight=37.0),
        ClusterEdge(source="M1", target="M2", weight=34.0),
        ClusterEdge(source="M1", target="M3", weight=36.0),
        ClusterEdge(source="M2", target="M3", weight=35.0),
    ]
    result = detect_endogamy(members, edges)
    assert result.endogamy_warning is True
    assert result.population_label == "AJ"
    assert result.pair_count == 6
    assert 33.0 < result.avg_pairwise_cm < 37.0
    assert result.avg_segments_per_member == 8.0


def test_endogamy_mennonite_threshold_band() -> None:
    """avg ~27 cM попадает в Mennonite band (≥25, <30)."""
    members = [ClusterMatch(match_id=f"M{i}") for i in range(3)]
    edges = [
        ClusterEdge(source="M0", target="M1", weight=27.0),
        ClusterEdge(source="M0", target="M2", weight=26.0),
        ClusterEdge(source="M1", target="M2", weight=28.0),
    ]
    result = detect_endogamy(members, edges)
    assert result.endogamy_warning is True
    assert result.population_label == "mennonite"


def test_endogamy_iberian_sephardic_threshold_band() -> None:
    """avg ~22 cM → Iberian-Sephardic (≥20, <25)."""
    members = [ClusterMatch(match_id=f"M{i}") for i in range(3)]
    edges = [
        ClusterEdge(source="M0", target="M1", weight=22.0),
        ClusterEdge(source="M0", target="M2", weight=21.0),
        ClusterEdge(source="M1", target="M2", weight=23.0),
    ]
    result = detect_endogamy(members, edges)
    assert result.endogamy_warning is True
    assert result.population_label == "iberian_sephardic"


def test_endogamy_low_cm_cluster_not_flagged() -> None:
    members = [ClusterMatch(match_id=f"M{i}") for i in range(3)]
    edges = [
        ClusterEdge(source="M0", target="M1", weight=12.0),
        ClusterEdge(source="M0", target="M2", weight=10.0),
        ClusterEdge(source="M1", target="M2", weight=15.0),
    ]
    result = detect_endogamy(members, edges)
    assert result.endogamy_warning is False
    assert result.population_label is None
    assert result.avg_pairwise_cm == pytest.approx((12 + 10 + 15) / 3)


def test_endogamy_too_few_pairs_consvervatively_unflagged() -> None:
    """min_pair_count gate: 2 рёбра (default min=3) → warning=False даже на heavy cM."""
    members = [ClusterMatch(match_id=f"M{i}") for i in range(3)]
    edges = [
        ClusterEdge(source="M0", target="M1", weight=40.0),
        ClusterEdge(source="M0", target="M2", weight=42.0),
    ]
    result = detect_endogamy(members, edges, min_pair_count=3)
    assert result.endogamy_warning is False
    # Но среднее всё равно посчитано — для UI debug'а.
    assert result.avg_pairwise_cm > 30.0


def test_endogamy_degraded_mode_only_binary_edges() -> None:
    """Если все рёбра binary fallback (weight=1.0), endogamy не определима."""
    members = [ClusterMatch(match_id=f"M{i}") for i in range(4)]
    edges = [
        ClusterEdge(source="M0", target="M1", weight=1.0),
        ClusterEdge(source="M0", target="M2", weight=1.0),
        ClusterEdge(source="M1", target="M2", weight=1.0),
        ClusterEdge(source="M2", target="M3", weight=1.0),
    ]
    result = detect_endogamy(members, edges)
    assert result.endogamy_warning is False
    assert result.population_label is None
    # avg_pairwise_cm = 0.0 потому что мы игнорируем weight=1.0 fallback'ы.
    assert result.avg_pairwise_cm == 0.0


def test_endogamy_filters_to_cluster_members() -> None:
    """edges за пределами members не должны влиять на детектор кластера."""
    members = [ClusterMatch(match_id=f"M{i}") for i in range(3)]
    edges = [
        ClusterEdge(source="M0", target="M1", weight=10.0),
        ClusterEdge(source="M0", target="M2", weight=12.0),
        ClusterEdge(source="M1", target="M2", weight=11.0),
        # Внешние рёбра — не должны подмешиваться:
        ClusterEdge(source="M0", target="OUTSIDER", weight=80.0),
        ClusterEdge(source="OUTSIDER", target="OUTSIDER2", weight=90.0),
    ]
    result = detect_endogamy(members, edges)
    assert result.pair_count == 3
    assert result.avg_pairwise_cm == pytest.approx(11.0)


def test_endogamy_empty_cluster_returns_safe_default() -> None:
    result = detect_endogamy([], [])
    assert result.endogamy_warning is False
    assert result.population_label is None
    assert result.pair_count == 0
