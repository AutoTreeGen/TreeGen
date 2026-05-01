"""Загрузка ``FamilyTraversal`` snapshot'а дерева из БД (Phase 10.7a / ADR-0068).

Caller — HTTP-handler для ``GET /trees/{tree_id}/relationships/{person_id}``.
Один SELECT по ``families`` + один по ``family_children`` + один по
``persons`` (sex). Tree-scope filter обязателен в каждом запросе:
без него BFS мог бы перепрыгнуть на персон чужих деревьев через
``persons.merged_into_person_id`` (хотя резолвер не следует за этим
полем — defence-in-depth).

Twin-detection (V1): два ребёнка одной семьи считаются близнецами,
если у них одинаковый ``birth_order > 0``. Дефолт ``birth_order = 0``
означает «порядок неизвестен» и НЕ создаёт ложных twin-пар. Если
импортёр не выставляет birth_order'ы (большинство GED-импортов так), —
twin-detection на этом дереве просто пустая (false negative ОК; false
positive недопустим). Future-work: фолбэк на одинаковый ``date_start``
у BIRT-event'ов; см. ADR-0068 §Trade-off/twin-detection.
"""

from __future__ import annotations

import uuid

from inference_engine.ego_relations import FamilyNode, FamilyTraversal
from shared_models.orm import Family, FamilyChild, Person
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def load_family_traversal(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
) -> FamilyTraversal:
    """Загрузить snapshot структуры дерева для эго-резолвера.

    Возвращает pure-data ``FamilyTraversal`` без ORM-объектов (резолвер —
    pure-functions пакет, не должен видеть SQLAlchemy).
    """
    families_res = await session.execute(
        select(Family.id, Family.husband_id, Family.wife_id).where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
        )
    )
    family_rows = families_res.all()
    family_ids = [row.id for row in family_rows]

    children_by_family: dict[uuid.UUID, list[uuid.UUID]] = {fid: [] for fid in family_ids}
    # Сохраняем (child_id, birth_order) для twin-detection.
    children_with_order: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = {
        fid: [] for fid in family_ids
    }
    if family_ids:
        fc_res = await session.execute(
            select(
                FamilyChild.family_id,
                FamilyChild.child_person_id,
                FamilyChild.birth_order,
            ).where(FamilyChild.family_id.in_(family_ids))
        )
        for fid, cid, order in fc_res.all():
            children_by_family[fid].append(cid)
            children_with_order[fid].append((cid, order))

    families: dict[uuid.UUID, FamilyNode] = {}
    for row in family_rows:
        families[row.id] = FamilyNode(
            family_id=row.id,
            husband_id=row.husband_id,
            wife_id=row.wife_id,
            child_ids=tuple(children_by_family[row.id]),
        )

    person_to_parent_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    person_to_spouse_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fam in families.values():
        for child_id in fam.child_ids:
            person_to_parent_families.setdefault(child_id, []).append(fam.family_id)
        for sup in (fam.husband_id, fam.wife_id):
            if sup is not None:
                person_to_spouse_families.setdefault(sup, []).append(fam.family_id)

    # Twin pairs: дети одной семьи с одинаковым ``birth_order > 0``.
    twin_pairs: set[frozenset[uuid.UUID]] = set()
    for ordered in children_with_order.values():
        # Группируем по birth_order (>0).
        by_order: dict[int, list[uuid.UUID]] = {}
        for child_id, order in ordered:
            if order > 0:
                by_order.setdefault(order, []).append(child_id)
        for siblings in by_order.values():
            if len(siblings) >= 2:
                # Все попарно twin'ы (тройни — каждые два считаются twin'ами).
                for i in range(len(siblings)):
                    for j in range(i + 1, len(siblings)):
                        twin_pairs.add(frozenset({siblings[i], siblings[j]}))

    # person_sex — один SELECT по дереву, ограниченный персонами,
    # реально появившимися в families/family_children.
    relevant_persons: set[uuid.UUID] = set()
    for fam in families.values():
        for sup in (fam.husband_id, fam.wife_id):
            if sup is not None:
                relevant_persons.add(sup)
        relevant_persons.update(fam.child_ids)

    person_sex: dict[uuid.UUID, str] = {}
    if relevant_persons:
        sex_res = await session.execute(
            select(Person.id, Person.sex).where(
                Person.tree_id == tree_id,
                Person.id.in_(relevant_persons),
                Person.deleted_at.is_(None),
            )
        )
        person_sex = {row.id: row.sex for row in sex_res.all()}

    return FamilyTraversal(
        families=families,
        person_to_parent_families=person_to_parent_families,
        person_to_spouse_families=person_to_spouse_families,
        person_sex=person_sex,
        twin_pairs=twin_pairs,
    )
