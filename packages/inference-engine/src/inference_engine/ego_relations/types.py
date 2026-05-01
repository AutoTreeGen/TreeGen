"""Типы для эго-резолвера: ``RelationshipPath`` (результат) и
``FamilyTraversal`` (вход BFS, snapshot структуры дерева).

``FamilyTraversal`` — pure-data контейнер. Caller (api-gateway) собирает
его из БД одним SELECT'ом по families/family_children/persons и передаёт
в ``relate()``. Так пакет остаётся I/O-free (см. ADR-0016).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FamilyNode:
    """Снапшот одной семьи для traversal'а.

    ``husband_id`` / ``wife_id`` — два supervisora; в однополых парах
    роли исторически фиксируются по GEDCOM (husband=первый, wife=второй),
    эго-резолвер sex-агностичен на уровне обхода (sex'ы используются
    только в humanize-стадии для выбора слова).
    """

    family_id: uuid.UUID
    husband_id: uuid.UUID | None
    wife_id: uuid.UUID | None
    child_ids: tuple[uuid.UUID, ...]


@dataclass(slots=True)
class FamilyTraversal:
    """Pure-data snapshot структуры дерева для BFS-обхода.

    Caller заполняет:

    - ``families``: ``family_id → FamilyNode``.
    - ``person_to_parent_families``: персона → семьи, где она ребёнок
      (одна ребёнок = одна семья обычно, но adoption даёт несколько).
    - ``person_to_spouse_families``: персона → семьи, где она supervisor
      (несколько браков = несколько семей).
    - ``person_sex``: персона → ``M``/``F``/``X``/``U``. Используется
      в humanize-стадии и в kind-кодинге (``wife`` vs ``husband``).
    - ``twin_pairs``: множество ``frozenset({a, b})`` — пары близнецов.
      Близнецы — дети одной семьи с одинаковым ``birth_order > 0`` ИЛИ
      явный twin-tag (caller решает, как заполнить).
    """

    families: dict[uuid.UUID, FamilyNode] = field(default_factory=dict)
    person_to_parent_families: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    person_to_spouse_families: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)
    person_sex: dict[uuid.UUID, str] = field(default_factory=dict)
    twin_pairs: set[frozenset[uuid.UUID]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class RelationshipPath:
    """Результат ``relate()`` — каноническое родство от ego к target.

    - ``kind`` — точечная нотация: ``self``, ``spouse``, ``parent``, ``child``,
      ``sibling``, ``wife.brother``, ``wife.mother.brother``, ``father.father``
      (=grandparent через отца), и т.д. Sex-aware (``wife`` vs ``husband``,
      ``mother`` vs ``father``, ``brother`` vs ``sister``, ``son`` vs
      ``daughter``).
    - ``degree`` — длина пути в рёбрах. ``self`` = 0; spouse = 1;
      wife.brother = 2; wife.mother.brother = 3.
    - ``via`` — промежуточные person id'ы (без endpoint'ов). Для
      ``wife.brother`` это ``[wife_id]``; для ``wife.mother.brother`` —
      ``[wife_id, wife_mother_id]``.
    - ``is_twin`` — True, если в пути есть sibling-ребро между близнецами.
      Сохраняем kind каноническим (``wife.brother``, не ``wife.twin_brother``)
      и помечаем флагом — humanize вставляет «twin» в нужное слово
      (см. ADR-0068 §Decision/twin disambiguation).
    - ``blood_relation`` — True, если в пути нет ни одного ``spouse``-ребра
      (т.е. кровный родственник, не in-law).
    """

    kind: str
    degree: int
    via: list[uuid.UUID]
    is_twin: bool
    blood_relation: bool
