"""BFS-резолвер родства от ego к target по структуре дерева.

Алгоритм: модифицированный Dijkstra с композитной ценой ``(hops, spouse_count)``.
Кратчайший путь по числу рёбер; при равенстве — путь с меньшим числом
spouse-рёбер (т.е. кровное родство предпочитается in-law'у). См. ADR-0068
§Decision/«BFS at query time».

Производительность: для типичного дерева <1000 персон обход <50ms.
Если станет узким местом — кэш по (tree_version, ego_id) hash'у; в Phase
10.7a оставляем naive recompute (ADR-0068 §Trade-off).
"""

from __future__ import annotations

import heapq
import uuid
from dataclasses import dataclass
from typing import Literal

from inference_engine.ego_relations.types import (
    FamilyTraversal,
    RelationshipPath,
)


class NoPathError(Exception):
    """Нет пути между ego и target в данном дереве.

    Бывает: target в дереве, но в disconnected-компоненте (например,
    imported GED двух не связанных родов в одно дерево).
    """


_EdgeKind = Literal["parent", "child", "spouse", "sibling"]


@dataclass(frozen=True, slots=True)
class _Edge:
    """Одно ребро traversal'а: тип отношения + куда ведёт."""

    kind: _EdgeKind
    target: uuid.UUID
    is_twin: bool = False  # релевантно только для kind='sibling'


def _neighbors(person_id: uuid.UUID, tree: FamilyTraversal) -> list[_Edge]:
    """Все рёбра, выходящие из ``person_id``: parent / child / spouse / sibling."""
    edges: list[_Edge] = []
    seen: set[tuple[str, uuid.UUID]] = set()  # дедуп: один и тот же spouse через разные браки

    def _add(kind: _EdgeKind, target: uuid.UUID, *, is_twin: bool = False) -> None:
        key = (kind, target)
        if key in seen:
            return
        seen.add(key)
        edges.append(_Edge(kind=kind, target=target, is_twin=is_twin))

    # Семьи, где person — ребёнок: parents + siblings (включая twins)
    for fid in tree.person_to_parent_families.get(person_id, []):
        family = tree.families.get(fid)
        if family is None:
            continue
        for parent_id in (family.husband_id, family.wife_id):
            if parent_id and parent_id != person_id:
                _add("parent", parent_id)
        for sib_id in family.child_ids:
            if sib_id != person_id:
                is_twin = frozenset({person_id, sib_id}) in tree.twin_pairs
                _add("sibling", sib_id, is_twin=is_twin)

    # Семьи, где person — supervisor: spouse + children
    for fid in tree.person_to_spouse_families.get(person_id, []):
        family = tree.families.get(fid)
        if family is None:
            continue
        for spouse_id in (family.husband_id, family.wife_id):
            if spouse_id and spouse_id != person_id:
                _add("spouse", spouse_id)
        for child_id in family.child_ids:
            _add("child", child_id)

    return edges


def _word_for(kind: _EdgeKind, target_sex: str) -> str:
    """Преобразует ребро + sex назначения в слово для kind-нотации.

    Sex-агностичный fallback (``spouse``/``parent``/``child``/``sibling``)
    используется при ``X`` (другой/intersex) или ``U`` (unknown).
    """
    sex = (target_sex or "U").upper()
    if kind == "spouse":
        if sex == "F":
            return "wife"
        if sex == "M":
            return "husband"
        return "spouse"
    if kind == "parent":
        if sex == "F":
            return "mother"
        if sex == "M":
            return "father"
        return "parent"
    if kind == "child":
        if sex == "F":
            return "daughter"
        if sex == "M":
            return "son"
        return "child"
    # sibling
    if sex == "F":
        return "sister"
    if sex == "M":
        return "brother"
    return "sibling"


def _build_path(edges: list[_Edge], tree: FamilyTraversal) -> RelationshipPath:
    """Собирает RelationshipPath из последовательности рёбер."""
    if not edges:
        return RelationshipPath(
            kind="self",
            degree=0,
            via=[],
            is_twin=False,
            blood_relation=True,
        )

    parts: list[str] = []
    is_twin = False
    for edge in edges:
        sex = tree.person_sex.get(edge.target, "U")
        parts.append(_word_for(edge.kind, sex))
        if edge.kind == "sibling" and edge.is_twin:
            is_twin = True

    via = [edge.target for edge in edges[:-1]]
    blood_relation = not any(edge.kind == "spouse" for edge in edges)

    return RelationshipPath(
        kind=".".join(parts),
        degree=len(edges),
        via=via,
        is_twin=is_twin,
        blood_relation=blood_relation,
    )


def relate(
    from_person_id: uuid.UUID,
    to_person_id: uuid.UUID,
    *,
    tree: FamilyTraversal,
) -> RelationshipPath:
    """BFS-резолвер: возвращает kind+degree+via+flags пути от ego к target.

    Возвращает ``RelationshipPath`` с ``kind='self'``, если from == to.
    Бросает ``NoPathError``, если пути нет (disconnected-компоненты).

    Алгоритм: Dijkstra по композитной цене ``(hops, spouse_count)``.
    Tuple-сравнение в heap'е делает primary-sort'ом число рёбер;
    secondary'ём — число spouse-рёбер (предпочтение кровному родству
    при равной длине). См. ADR-0068 §Decision/preference.

    Args:
        from_person_id: ego (обычно ``trees.owner_person_id``).
        to_person_id: target — кого именуем.
        tree: snapshot структуры дерева, см. ``FamilyTraversal``.

    Returns:
        RelationshipPath. ``kind='self'`` для ego==target.

    Raises:
        NoPathError: target в другом disconnected-компоненте дерева.
    """
    if from_person_id == to_person_id:
        return _build_path([], tree)

    # heap: (hops, spouse_count, tiebreak_seq, current_id, path_edges)
    # tiebreak_seq нужен только для детерминированности — UUID не сравниваются.
    counter = 0
    initial: tuple[int, int, int, uuid.UUID, tuple[_Edge, ...]] = (
        0,
        0,
        counter,
        from_person_id,
        (),
    )
    queue: list[tuple[int, int, int, uuid.UUID, tuple[_Edge, ...]]] = [initial]
    best_cost: dict[uuid.UUID, tuple[int, int]] = {from_person_id: (0, 0)}

    while queue:
        hops, sp, _, current, path = heapq.heappop(queue)

        if current == to_person_id:
            return _build_path(list(path), tree)

        if (hops, sp) > best_cost.get(current, (10**9, 10**9)):
            continue

        for edge in _neighbors(current, tree):
            new_hops = hops + 1
            new_sp = sp + (1 if edge.kind == "spouse" else 0)
            cost = (new_hops, new_sp)
            if cost < best_cost.get(edge.target, (10**9, 10**9)):
                best_cost[edge.target] = cost
                counter += 1
                heapq.heappush(
                    queue,
                    (new_hops, new_sp, counter, edge.target, (*path, edge)),
                )

    msg = (
        f"no relationship path from {from_person_id} to {to_person_id} "
        "(disconnected components in tree)"
    )
    raise NoPathError(msg)
