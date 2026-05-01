"""Co-match graph build (Phase 6.7a / ADR-0063).

Граф для AutoCluster алгоритма:

* **Узлы:** matches kit-owner'а (не сам owner — мы кластеризуем его
  matches между собой, чтобы найти ветви семьи).
* **Рёбра:** пары matches (A, B), являющиеся mutual shared matches с
  ненулевой shared_cm. Вес ребра = ``shared_cm`` (cM, который A и B
  делят между собой), отфильтрованный порогом ``min_shared_cm``.

Платформа-зависимость: shared_cm между двумя non-owner matches
доступен **не везде**:

* MyHeritage явно сообщает shared_cm для каждой пары shared matches.
* Ancestry даёт только membership (есть ли пара в shared-matches set'е),
  без cM. В таком случае :func:`build_co_match_graph` принимает
  отсутствие ``pairwise_cm`` и присваивает ребру weight = 1.0 (binary).
* 23andMe / FTDNA — варьирует по версии экспорта.

Алгоритм Leiden с binary weights всё ещё работает, но качество
detection'а communities заметно падает; в ADR-0063 это документировано
как «degraded mode».
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Industry-default нижний порог для значимого co-match-сегмента.
# Пары matches с shared_cm < 8 cM в подавляющем большинстве — IBD-noise
# или endogamy false-positive (см. ADR-0014, ADR-0063 §«Trade-off:
# threshold choice»). Caller может переопределить.
DEFAULT_MIN_SHARED_CM: Final[float] = 8.0


class ClusterMatch(BaseModel):
    """Один match как input для clustering'а.

    ``match_id`` — стабильный идентификатор (может быть UUID-string или
    platform-id, зависит от caller'а; алгоритм его как opaque key).

    ``total_cm`` — cM с kit-owner'ом (для filtering / endogamy heuristic;
    не для построения рёбер). ``segments_with_owner`` — количество
    отдельных IBD-сегментов между match и owner'ом, сильный сигнал для
    endogamy (много коротких сегментов вместо одного длинного).

    ``pairwise_cm`` — словарь {other_match_id: shared_cm} с другими
    matches в той же match-list. Только пары, где у нас есть числовое
    значение (не binary membership). Двунаправленность — ответственность
    caller'а: если он положил ``A.pairwise_cm = {B: 25.0}``, то для
    симметрии должно быть ``B.pairwise_cm = {A: 25.0}``. :func:`build_co_match_graph`
    проверяет это и берёт max'ом, чтобы не зависеть от направления.

    ``shared_match_ids`` — fallback для платформ без числовой пары:
    bare set «есть co-match», без cM. Если пара ``B ∈ A.shared_match_ids``
    есть, но ``A.pairwise_cm`` не содержит B, ребру даётся weight = 1.0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str = Field(..., min_length=1)
    total_cm: float | None = Field(default=None, ge=0.0)
    segments_with_owner: int | None = Field(default=None, ge=0)
    pairwise_cm: dict[str, float] = Field(default_factory=dict)
    shared_match_ids: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def _no_self_relations(self) -> ClusterMatch:
        """Запрещаем self-loops (A в своих shared_match_ids / pairwise_cm)."""
        if self.match_id in self.pairwise_cm:
            msg = f"ClusterMatch.{self.match_id}: pairwise_cm contains self-loop"
            raise ValueError(msg)
        if self.match_id in self.shared_match_ids:
            msg = f"ClusterMatch.{self.match_id}: shared_match_ids contains self-loop"
            raise ValueError(msg)
        for cm in self.pairwise_cm.values():
            if cm < 0:
                msg = f"ClusterMatch.{self.match_id}: negative pairwise_cm {cm}"
                raise ValueError(msg)
        return self


class ClusterEdge(BaseModel):
    """Ребро между двумя matches с весом.

    Каноническая ориентация: ``source < target`` (лексикографически),
    чтобы пара (A,B) и (B,A) не считались дважды.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: str
    weight: float = Field(..., gt=0.0)

    @model_validator(mode="after")
    def _canonical_order(self) -> ClusterEdge:
        if self.source >= self.target:
            msg = (
                f"ClusterEdge requires source < target lexicographically "
                f"(got source={self.source}, target={self.target})"
            )
            raise ValueError(msg)
        return self


def build_co_match_graph(
    matches: list[ClusterMatch],
    min_shared_cm: float = DEFAULT_MIN_SHARED_CM,
) -> tuple[list[str], list[ClusterEdge]]:
    """Build co-match graph from a list of matches.

    Args:
        matches: Список matches kit-owner'а.
        min_shared_cm: Минимальная shared_cm между двумя matches для
            создания ребра. Применяется только к числовым парам;
            binary-membership пары (через ``shared_match_ids``) всегда
            проходят как weight=1.0, потому что у нас нет cM-значения
            для фильтрации.

    Returns:
        Кортеж ``(node_ids, edges)``. ``node_ids`` — отсортированный
        список match_id (стабильно для тестов). ``edges`` — список
        :class:`ClusterEdge` с canonical orientation source < target.
    """
    if min_shared_cm < 0:
        msg = "min_shared_cm must be non-negative"
        raise ValueError(msg)
    by_id: dict[str, ClusterMatch] = {m.match_id: m for m in matches}
    if len(by_id) != len(matches):
        msg = "build_co_match_graph: duplicate match_id in input"
        raise ValueError(msg)
    node_ids = sorted(by_id)

    # Собираем pair-level максимум cM (a ↔ b симметрично).
    pair_cm: dict[tuple[str, str], float] = {}
    pair_binary: set[tuple[str, str]] = set()
    for m in matches:
        for other_id, cm in m.pairwise_cm.items():
            if other_id not in by_id:
                # Caller дал ребро на match'а, которого нет в input'е;
                # отбрасываем тихо — это типично для частичных match-list'ов.
                continue
            key = (m.match_id, other_id) if m.match_id < other_id else (other_id, m.match_id)
            existing = pair_cm.get(key)
            if existing is None or cm > existing:
                pair_cm[key] = cm
        for other_id in m.shared_match_ids:
            if other_id not in by_id:
                continue
            key = (m.match_id, other_id) if m.match_id < other_id else (other_id, m.match_id)
            pair_binary.add(key)

    edges: list[ClusterEdge] = []
    for key, cm in pair_cm.items():
        if cm < min_shared_cm:
            continue
        edges.append(ClusterEdge(source=key[0], target=key[1], weight=cm))

    seen_pairs = set(pair_cm)
    for key in pair_binary:
        if key in seen_pairs:
            # Уже добавили (или отбросили под порогом cM) — не дублируем
            # binary-fallback'ом.
            continue
        edges.append(ClusterEdge(source=key[0], target=key[1], weight=1.0))

    edges.sort(key=lambda e: (e.source, e.target))
    return node_ids, edges
