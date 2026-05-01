"""Типы для Safe Merge applier (Phase 5.7b).

Все модели — Pydantic v2. Identity-ключ для персон в DiffReport — строковый
``xref`` (стабильный идентификатор уровня GEDCOM, ``@I123@`` без обрамляющих
@). DB-адаптер (:mod:`gedcom_parser.merge.applier`) сам резолвит его в
``persons.id`` (UUID) через ``persons.gedcom_xref`` колонку.

.. note:: Phase 5.7a dependency
   ``DiffReport`` определён локально, потому что 5.7a (``gedcom_parser.diff``)
   ещё не landed в main. Когда landed — переключиться на
   ``from gedcom_parser.diff import DiffReport`` с тем же набором полей.
   Ожидаемая совместимость:

   * ``persons_added`` / ``persons_modified`` / ``persons_removed``
   * ``relations_added`` / ``relations_removed``

   Любое расхождение полей будет жёстко поймано тестами 5.7a при cut-over'e.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# In-memory snapshot целевого дерева
# ---------------------------------------------------------------------------

#: Допустимые типы родственных связей в diff'е.
#:
#: ``parent_child`` — отношение «родитель → ребёнок» (направленное; person_a —
#: родитель, person_b — ребёнок). На стороне БД материализуется как пара
#: ``Family`` + ``FamilyChild`` (если родитель не привязан к Family — создаётся
#: stub-Family). ``spouse`` — симметричное отношение, материализуется одной
#: ``Family`` row с ``husband_id``/``wife_id`` (роль husband/wife определяется
#: по ``person.sex`` владельца).
RelationType = Literal["parent_child", "spouse"]


class PersonRecord(BaseModel):
    """Запись персоны в in-memory snapshot.

    ``fields`` — плоский словарь известных полей: ``sex``, ``gedcom_xref``,
    ``birth_date_raw``, ``death_date_raw``, и т.д. Конкретный набор не
    enforced'ится здесь — он определяется тем, что DB-адаптер умеет
    persisть.
    """

    model_config = ConfigDict(extra="forbid")

    xref: str
    fields: dict[str, Any] = Field(default_factory=dict)


class RelationRecord(BaseModel):
    """In-memory связь между двумя персонами по xref'ам."""

    model_config = ConfigDict(extra="forbid")

    relation_type: RelationType
    person_a: str
    person_b: str


class TreeSnapshot(BaseModel):
    """Plain-Python snapshot текущего состояния target-дерева.

    Используется чисто-функциональным applier'ом для проверки конфликтов и
    подсчёта планируемых изменений. DB-адаптер ``apply_diff_to_session``
    строит этот snapshot из ORM-моделей перед прогоном.
    """

    model_config = ConfigDict(extra="forbid")

    persons: dict[str, PersonRecord] = Field(default_factory=dict)
    relations: list[RelationRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# DiffReport — стаб для Phase 5.7a
# ---------------------------------------------------------------------------


class FieldChange(BaseModel):
    """Один field-level diff: что было в target и что предлагается из right."""

    model_config = ConfigDict(extra="forbid")

    before: Any = None
    after: Any = None


class PersonAdd(BaseModel):
    """Новая персона из right, которой нет в target."""

    model_config = ConfigDict(extra="forbid")

    xref: str
    fields: dict[str, Any] = Field(default_factory=dict)


class PersonModify(BaseModel):
    """Изменения полей существующей в target персоны.

    ``target_xref`` — gedcom_xref персоны в target-дереве. ``field_changes``
    — словарь ``field_name → FieldChange``. ``before`` обязан совпадать с
    тем, что сейчас в target — иначе fail.
    """

    model_config = ConfigDict(extra="forbid")

    target_xref: str
    field_changes: dict[str, FieldChange] = Field(default_factory=dict)


class PersonRemove(BaseModel):
    """Soft-delete существующей персоны."""

    model_config = ConfigDict(extra="forbid")

    target_xref: str


class RelationAdd(BaseModel):
    """Новая связь между двумя персонами.

    Оба ``person_a_xref`` и ``person_b_xref`` должны существовать либо в
    target, либо среди ``persons_added`` в этом же diff. Иначе —
    ``missing_anchor`` (фатальная ошибка, abort'ит весь merge).
    """

    model_config = ConfigDict(extra="forbid")

    relation_type: RelationType
    person_a_xref: str
    person_b_xref: str


class RelationRemove(BaseModel):
    """Удаление существующей связи."""

    model_config = ConfigDict(extra="forbid")

    relation_type: RelationType
    person_a_xref: str
    person_b_xref: str


class DiffReport(BaseModel):
    """Diff между двумя GEDCOM-деревьями (стаб для 5.7a).

    TODO(5.7a-cutover): заменить на ``from gedcom_parser.diff import
    DiffReport`` после merge'а 5.7a в main.
    """

    model_config = ConfigDict(extra="forbid")

    persons_added: list[PersonAdd] = Field(default_factory=list)
    persons_modified: list[PersonModify] = Field(default_factory=list)
    persons_removed: list[PersonRemove] = Field(default_factory=list)
    relations_added: list[RelationAdd] = Field(default_factory=list)
    relations_removed: list[RelationRemove] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# MergePolicy / MergeResult / Conflict / Change / Audit
# ---------------------------------------------------------------------------

#: Стратегия разрешения soft-конфликтов:
#:
#: * ``prefer_left`` — в target поле остаётся как есть, изменение из diff
#:   игнорируется. Удобно для read-only слияний (UI «merge from external
#:   tree, keep my data»).
#: * ``prefer_right`` — поле в target перезаписывается значением из diff.
#: * ``manual`` — конфликт записывается в ``skipped``, изменение не
#:   применяется. Дальнейшее решение пользователь принимает в UI.
#: * ``skip`` — алиас ``manual`` без UI-семантики (просто пропустить и
#:   залогировать). Используется в batch'ах, где manual review не
#:   планируется.
OnConflict = Literal["prefer_left", "prefer_right", "manual", "skip"]


class MergePolicy(BaseModel):
    """Политика применения diff'а.

    ``on_conflict`` — что делать при field/relation-overlap. ``actor_user_id``
    — кто инициировал merge (uuid as str чтобы Pydantic не привязывался к
    UUID-типу), пишется в ``Audit.actor_user_id`` для трейсинга.
    """

    model_config = ConfigDict(extra="forbid")

    on_conflict: OnConflict = "manual"
    actor_user_id: str | None = None


ConflictType = Literal["field_overlap", "relation_overlap", "missing_anchor"]


class Conflict(BaseModel):
    """Описание конфликта между diff'ом и текущим target.

    ``kind`` — тип. Для ``field_overlap``: ``target_xref`` + ``field`` +
    ``left_value`` + ``right_value``. Для ``relation_overlap``: ``person_a``
    + ``person_b`` + ``relation_type`` + детализация. Для
    ``missing_anchor``: ``person_a`` или ``person_b`` указывает на
    несуществующий xref.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ConflictType
    target_xref: str | None = None
    field: str | None = None
    left_value: Any = None
    right_value: Any = None
    person_a_xref: str | None = None
    person_b_xref: str | None = None
    relation_type: RelationType | None = None
    detail: str | None = None


ChangeKind = Literal[
    "person_added",
    "person_field_updated",
    "person_removed",
    "relation_added",
    "relation_removed",
]


class Change(BaseModel):
    """Описание одного успешно применённого изменения.

    Используется и для возврата в ответе, и как payload для
    DB-материализации в :func:`apply_diff_to_session`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ChangeKind
    xref: str | None = None
    field: str | None = None
    new_value: Any = None
    relation_type: RelationType | None = None
    person_a_xref: str | None = None
    person_b_xref: str | None = None


AuditAction = Literal[
    "applied",
    "skipped_field_overlap",
    "skipped_relation_overlap",
    "aborted_missing_anchor",
    "applied_prefer_left",
    "applied_prefer_right",
]


class Audit(BaseModel):
    """Запись audit-лога: что и почему произошло с конкретным элементом diff'а."""

    model_config = ConfigDict(extra="forbid")

    action: AuditAction
    detail: str
    target_xref: str | None = None
    field: str | None = None
    actor_user_id: str | None = None


class MergeResult(BaseModel):
    """Результат прогона :func:`apply_diff_pure` или :func:`apply_diff_to_session`.

    ``applied`` — изменения, которые либо уже применены к БД (для async-варианта),
    либо запланированы к применению (для pure-варианта). ``skipped`` —
    конфликты, не применённые согласно policy. ``log`` — полный
    audit-trail (включает и applied, и skipped, и aborted-причину).

    ``aborted=True`` означает: применение DB-операций НЕ происходило (или
    было откатано transaction'ом). Это происходит ровно при наличии
    ``missing_anchor`` в diff'е.
    """

    model_config = ConfigDict(extra="forbid")

    applied: list[Change] = Field(default_factory=list)
    skipped: list[Conflict] = Field(default_factory=list)
    log: list[Audit] = Field(default_factory=list)
    aborted: bool = False
    abort_reason: str | None = None
