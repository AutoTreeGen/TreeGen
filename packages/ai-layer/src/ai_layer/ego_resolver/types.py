"""Dataclasses для ego_resolver: вход (``TreeContext``) и выход
(``ResolvedPerson`` + ``RelStep``).

``TreeContext`` оборачивает 10.7a ``FamilyTraversal`` (структура дерева)
и добавляет ``persons: dict[id, PersonNames]`` для name-index'а. Sex
берётся из ``traversal.person_sex`` чтобы не дублировать source-of-truth.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from inference_engine.ego_relations import FamilyTraversal

# Ребро в kind-нотации 10.7a резолвера: spouse / parent / child / sibling.
# sex_hint — пол target'а из лексикона ввода (``wife`` → F, ``mother`` → F,
# ``brother`` → M); ``None`` значит «без указания» (``spouse``, ``parent``,
# ``child``, ``sibling`` без пола). Используется walker'ом для фильтрации
# рёбер при множественных кандидатах.
RelKind = Literal["spouse", "parent", "child", "sibling"]
SexHint = Literal["M", "F"]


@dataclass(frozen=True, slots=True)
class RelStep:
    """Одно ребро в parsed-reference path'е.

    Attributes:
        kind: Тип отношения. Совместим с ``_EdgeKind`` из 10.7a резолвера.
        sex_hint: Пол target'а если ввод его задаёт (``wife`` → ``"F"``,
            ``husband`` → ``"M"``, ``brother`` → ``"M"``); ``None`` для
            sex-нейтральных слов (``spouse`` / ``parent`` / ``child`` /
            ``sibling``).
        word: Исходный токен из ввода («wife», «жены», «mother's»).
            Сохраняем для debug / UI explainability.
    """

    kind: RelKind
    sex_hint: SexHint | None
    word: str


@dataclass(frozen=True, slots=True)
class PersonNames:
    """Searchable name-record персоны для name-index'а.

    Все поля опциональны — реальные GEDCOM содержат записи без given или
    surname'а (например, неподписанные «Mrs. Smith»). Caller передаёт всё
    что есть в shared-models ``Name``-таблице.

    Attributes:
        person_id: UUID персоны.
        given: Основное given name (любой script).
        surname: Основная фамилия.
        full_names: Полные формы для full-string match'а («Dvora Levin»).
            Может содержать duplicates / multiple языков (caller не дедупит).
        aliases: Альтернативы — nicknames, romanized varianты, maiden
            surnames, иврит-имена. Каждое сравнивается отдельно.
    """

    person_id: uuid.UUID
    given: str | None = None
    surname: str | None = None
    full_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(slots=True)
class TreeContext:
    """Snapshot структуры дерева + name-records для ego_resolver'а.

    Caller (api-gateway или voice/chat pipeline) собирает один раз на
    запрос; pure-data (никаких ORM / lazy-load), потому что резолвер
    обязан быть thread-safe и I/O-free.

    Attributes:
        traversal: 10.7a ``FamilyTraversal`` — рёбра между персонами +
            ``person_sex`` для sex-aware filtering'а.
        persons: ``person_id → PersonNames`` для всех персон в дереве.
            Persons, которых нет здесь, всё равно резолвятся по traversal'у
            (структурный path работает), но не находятся по имени.
    """

    traversal: FamilyTraversal
    persons: Mapping[uuid.UUID, PersonNames] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedPerson:
    """Результат :func:`resolve_reference`.

    Attributes:
        person_id: UUID выбранного кандидата (top-1).
        confidence: В [0.0, 1.0]. ``1.0`` — unambiguous direct match
            (один структурный кандидат для пути или единственный exact-name
            hit). ``< 1.0`` — несколько кандидатов или fuzzy/translit-name
            match.
        path: Последовательность ``RelStep`` от ego к target. Пустой —
            если резолв был чисто по имени (``"Dvora"``) без relationship-
            токенов.
        alternatives: Top-N (без выбранного top-1) кандидатов. Пустой —
            если резолв уникален. Используется UI-слоем для disambiguation
            prompt'а («Did you mean Dvora Levin or Dvora Cohen?»).
    """

    person_id: uuid.UUID
    confidence: float
    path: tuple[RelStep, ...]
    alternatives: tuple[ResolvedPerson, ...] = ()


__all__ = [
    "PersonNames",
    "RelKind",
    "RelStep",
    "ResolvedPerson",
    "SexHint",
    "TreeContext",
]
