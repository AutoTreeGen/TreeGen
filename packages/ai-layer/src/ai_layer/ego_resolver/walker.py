"""Walker по ``FamilyTraversal``: разворачивает ``[RelStep, ...]`` в множество
кандидатов-person_id'ов от ego.

В отличие от 10.7a ``relate()`` (path-finding между двумя известными ID'ами),
здесь обратная задача — given anchor + relationship-spec, найти все персоны,
удовлетворяющие spec'у. Может вернуть 0 (никого нет), 1 (уникально) или N
(несколько детей / несколько братьев) кандидатов.

Pure-function: только структура из :class:`FamilyTraversal`.
"""

from __future__ import annotations

import uuid

from inference_engine.ego_relations import FamilyTraversal

from ai_layer.ego_resolver.types import RelKind, RelStep, SexHint


def _spouses_of(person_id: uuid.UUID, tree: FamilyTraversal) -> set[uuid.UUID]:
    """Все супруги ``person_id`` через все его браки."""
    out: set[uuid.UUID] = set()
    for fid in tree.person_to_spouse_families.get(person_id, []):
        family = tree.families.get(fid)
        if family is None:
            continue
        for sp in (family.husband_id, family.wife_id):
            if sp and sp != person_id:
                out.add(sp)
    return out


def _parents_of(person_id: uuid.UUID, tree: FamilyTraversal) -> set[uuid.UUID]:
    """Все родители (биологические + adoption — caller решает заполнение)."""
    out: set[uuid.UUID] = set()
    for fid in tree.person_to_parent_families.get(person_id, []):
        family = tree.families.get(fid)
        if family is None:
            continue
        for parent in (family.husband_id, family.wife_id):
            if parent:
                out.add(parent)
    return out


def _children_of(person_id: uuid.UUID, tree: FamilyTraversal) -> set[uuid.UUID]:
    """Все дети ``person_id`` через все его браки."""
    out: set[uuid.UUID] = set()
    for fid in tree.person_to_spouse_families.get(person_id, []):
        family = tree.families.get(fid)
        if family is None:
            continue
        out.update(family.child_ids)
    return out


def _siblings_of(person_id: uuid.UUID, tree: FamilyTraversal) -> set[uuid.UUID]:
    """Sibling = ребёнок одной из родительских семей (минус сам person)."""
    out: set[uuid.UUID] = set()
    for fid in tree.person_to_parent_families.get(person_id, []):
        family = tree.families.get(fid)
        if family is None:
            continue
        for sib in family.child_ids:
            if sib != person_id:
                out.add(sib)
    return out


def _expand(person_id: uuid.UUID, kind: RelKind, tree: FamilyTraversal) -> set[uuid.UUID]:
    """Один шаг: множество соседей по типу ребра."""
    if kind == "spouse":
        return _spouses_of(person_id, tree)
    if kind == "parent":
        return _parents_of(person_id, tree)
    if kind == "child":
        return _children_of(person_id, tree)
    if kind == "sibling":
        return _siblings_of(person_id, tree)
    msg = f"unsupported RelKind: {kind!r}"  # pragma: no cover — Literal-narrowed
    raise ValueError(msg)


def _filter_by_sex(
    candidates: set[uuid.UUID],
    sex_hint: SexHint | None,
    tree: FamilyTraversal,
) -> set[uuid.UUID]:
    """Если sex_hint задан — оставляем только персон с матчингом sex'а.

    Персоны с unknown sex (``"U"``, ``"X"``, missing) проходят фильтр —
    отсекать их too aggressive (legacy GED имеют много sex=U), и UI всё
    равно даст пользователю выбрать из alternatives.
    """
    if sex_hint is None:
        return candidates
    return {
        pid
        for pid in candidates
        if tree.person_sex.get(pid, "U").upper() in {sex_hint, "U", "X", ""}
    }


def walk_path(
    anchor_id: uuid.UUID,
    steps: tuple[RelStep, ...],
    tree: FamilyTraversal,
) -> set[uuid.UUID]:
    """Разворачивает relationship-path от ego в множество кандидатов.

    Args:
        anchor_id: Ego (обычно ``trees.owner_person_id`` из 10.7a).
        steps: Канонический ego→target path. Пустой кортеж = «сам ego»
            (возвращает ``{anchor_id}``).
        tree: Snapshot структуры дерева (10.7a ``FamilyTraversal``).

    Returns:
        ``set[UUID]`` всех персон, удовлетворяющих path'у с учётом sex_hint
        фильтра. Пустой set — путь disconnected или фильтр всё отсёк.
    """
    if not steps:
        return {anchor_id}

    frontier: set[uuid.UUID] = {anchor_id}
    for step in steps:
        next_frontier: set[uuid.UUID] = set()
        for pid in frontier:
            next_frontier.update(_expand(pid, step.kind, tree))
        next_frontier = _filter_by_sex(next_frontier, step.sex_hint, tree)
        frontier = next_frontier
        if not frontier:
            return frontier
    # ego никогда не возвращаем сам себе («моя жена» через self-loop невозможно,
    # но защищаемся от циклов в данных).
    frontier.discard(anchor_id)
    return frontier


__all__ = ["walk_path"]
