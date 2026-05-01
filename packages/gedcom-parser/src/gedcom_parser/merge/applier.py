"""Реализация Safe Merge applier'а (Phase 5.7b).

Содержит две функции:

* :func:`apply_diff_pure` — чистая функция без побочных эффектов. Принимает
  in-memory ``TreeSnapshot`` и ``DiffReport``, возвращает ``MergeResult``
  с планируемыми изменениями (``applied``), конфликтами (``skipped``) и
  audit-логом (``log``). Не записывает в БД, не зависит от SQLAlchemy.
* :func:`apply_diff_to_session` — async-обёртка, которая загружает текущее
  состояние target-дерева из БД, прогоняет :func:`apply_diff_pure`, и при
  отсутствии fatal-конфликтов материализует изменения в БД внутри
  ``session.begin_nested()``.

Атомарность: ``missing_anchor`` (right ссылается на xref, которого нет ни в
target, ни в ``persons_added``) — фатальная ошибка, abort'ит весь merge
БЕЗ ЕДИНОЙ DB-записи. Soft-конфликты (``field_overlap`` / ``relation_overlap``)
разрешаются согласно policy и применяются.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from gedcom_parser.merge.types import (
    Audit,
    Change,
    Conflict,
    DiffReport,
    MergePolicy,
    MergeResult,
    PersonRecord,
    RelationRecord,
    TreeSnapshot,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Pure applier
# ---------------------------------------------------------------------------


def _check_missing_anchors(
    diff: DiffReport,
    target: TreeSnapshot,
) -> list[Conflict]:
    """Проверить, что все relation'ы и modify'и ссылаются на существующие xref'ы.

    «Существующий» = либо есть в ``target.persons``, либо добавляется в
    этом же diff (``persons_added``). Если ни то, ни другое — это
    ``missing_anchor``.
    """
    known: set[str] = set(target.persons.keys())
    known.update(p.xref for p in diff.persons_added)

    conflicts: list[Conflict] = []

    for rel_add in diff.relations_added:
        for xref in (rel_add.person_a_xref, rel_add.person_b_xref):
            if xref not in known:
                conflicts.append(
                    Conflict(
                        kind="missing_anchor",
                        person_a_xref=rel_add.person_a_xref,
                        person_b_xref=rel_add.person_b_xref,
                        relation_type=rel_add.relation_type,
                        detail=(
                            f"relation references unknown xref '{xref}': "
                            f"not in target and not in persons_added"
                        ),
                    )
                )

    for rel in diff.relations_removed:
        for xref in (rel.person_a_xref, rel.person_b_xref):
            if xref not in target.persons:
                conflicts.append(
                    Conflict(
                        kind="missing_anchor",
                        person_a_xref=rel.person_a_xref,
                        person_b_xref=rel.person_b_xref,
                        relation_type=rel.relation_type,
                        detail=(
                            f"relation_removed references xref '{xref}' "
                            f"that does not exist in target"
                        ),
                    )
                )

    for mod in diff.persons_modified:
        if mod.target_xref not in target.persons:
            conflicts.append(
                Conflict(
                    kind="missing_anchor",
                    target_xref=mod.target_xref,
                    detail=(
                        f"persons_modified references xref '{mod.target_xref}' "
                        f"that does not exist in target"
                    ),
                )
            )

    for rem in diff.persons_removed:
        if rem.target_xref not in target.persons:
            conflicts.append(
                Conflict(
                    kind="missing_anchor",
                    target_xref=rem.target_xref,
                    detail=(
                        f"persons_removed references xref '{rem.target_xref}' "
                        f"that does not exist in target"
                    ),
                )
            )

    return conflicts


def _resolve_field_conflict(
    *,
    target_value: Any,
    proposed_before: Any,
    proposed_after: Any,
    policy: MergePolicy,
    target_xref: str,
    field: str,
) -> tuple[bool, Any, Audit, Conflict | None]:
    """Решить, применять ли изменение поля.

    Returns:
        (apply, new_value, audit_entry, conflict_or_none)

        * ``apply=False`` — изменение пропущено; conflict не None,
          audit зафиксирует skipped_field_overlap.
        * ``apply=True`` — изменение применить, ``new_value`` — что писать.
    """
    # Если target пустой и proposed_before пустой — это новое значение,
    # просто применяем (нет конфликта).
    if target_value is None and (proposed_before is None or proposed_before == target_value):
        return (
            True,
            proposed_after,
            Audit(
                action="applied",
                detail=f"field '{field}' set to {proposed_after!r}",
                target_xref=target_xref,
                field=field,
                actor_user_id=policy.actor_user_id,
            ),
            None,
        )

    # Если target совпадает с proposed_before — изменение «свежее», применяем.
    if target_value == proposed_before:
        return (
            True,
            proposed_after,
            Audit(
                action="applied",
                detail=f"field '{field}' updated {target_value!r} → {proposed_after!r}",
                target_xref=target_xref,
                field=field,
                actor_user_id=policy.actor_user_id,
            ),
            None,
        )

    # Если target уже равен proposed_after — no-op.
    if target_value == proposed_after:
        return (
            False,
            target_value,
            Audit(
                action="applied",
                detail=f"field '{field}' already at {proposed_after!r}; no-op",
                target_xref=target_xref,
                field=field,
                actor_user_id=policy.actor_user_id,
            ),
            None,
        )

    # Конфликт: target имеет своё значение, отличное и от before, и от after.
    conflict = Conflict(
        kind="field_overlap",
        target_xref=target_xref,
        field=field,
        left_value=target_value,
        right_value=proposed_after,
        detail=(
            f"target has '{target_value!r}', diff proposes "
            f"'{proposed_before!r}' → '{proposed_after!r}'"
        ),
    )

    if policy.on_conflict == "prefer_left":
        return (
            False,
            target_value,
            Audit(
                action="applied_prefer_left",
                detail=f"field '{field}' kept at {target_value!r} (prefer_left)",
                target_xref=target_xref,
                field=field,
                actor_user_id=policy.actor_user_id,
            ),
            None,
        )
    if policy.on_conflict == "prefer_right":
        return (
            True,
            proposed_after,
            Audit(
                action="applied_prefer_right",
                detail=f"field '{field}' overwritten {target_value!r} → {proposed_after!r}",
                target_xref=target_xref,
                field=field,
                actor_user_id=policy.actor_user_id,
            ),
            None,
        )
    # manual / skip — конфликт, не применяем.
    return (
        False,
        target_value,
        Audit(
            action="skipped_field_overlap",
            detail=conflict.detail or f"field '{field}' conflict",
            target_xref=target_xref,
            field=field,
            actor_user_id=policy.actor_user_id,
        ),
        conflict,
    )


def _relation_key(rel: RelationRecord) -> tuple[str, str, str]:
    """Каноничный ключ связи. Для ``spouse`` — порядок xref'ов сортируется
    (отношение симметричное). Для ``parent_child`` — порядок сохраняется
    (направленное)."""
    if rel.relation_type == "spouse":
        a, b = sorted([rel.person_a, rel.person_b])
        return ("spouse", a, b)
    return ("parent_child", rel.person_a, rel.person_b)


def apply_diff_pure(
    target: TreeSnapshot,
    diff: DiffReport,
    policy: MergePolicy,
) -> MergeResult:
    """Чисто-функциональный applier — не пишет в БД, только планирует изменения.

    Семантика:

    1. Pre-flight: ищем все ``missing_anchor``. Если хоть один найден —
       ``aborted=True``, ``applied=[]``, в ``skipped`` все anchor-конфликты,
       в ``log`` запись ``aborted_missing_anchor``.
    2. Иначе применяем по очереди: ``persons_added`` → ``persons_modified``
       → ``persons_removed`` → ``relations_added`` → ``relations_removed``.
       Каждый soft-конфликт разрешается через :func:`_resolve_field_conflict`
       или симметричный путь для relation'ов.
    """
    result = MergeResult()

    # Phase 1: pre-flight anchor check. Эти конфликты фатальны.
    anchor_conflicts = _check_missing_anchors(diff, target)
    if anchor_conflicts:
        result.aborted = True
        result.skipped = anchor_conflicts
        result.abort_reason = (
            f"missing_anchor: {len(anchor_conflicts)} broken reference(s); "
            f"merge aborted before any DB writes"
        )
        for c in anchor_conflicts:
            result.log.append(
                Audit(
                    action="aborted_missing_anchor",
                    detail=c.detail or "missing anchor",
                    target_xref=c.target_xref,
                    actor_user_id=policy.actor_user_id,
                )
            )
        return result

    # Phase 2: persons_added — добавляем, если xref ещё не занят. Если занят —
    # это field-конфликт по умолчанию, разрешаем по policy.
    for add in diff.persons_added:
        if add.xref in target.persons:
            existing = target.persons[add.xref].fields
            # Сравниваем поле за полем — если все совпадают, no-op.
            differing = {
                k: (existing.get(k), v) for k, v in add.fields.items() if existing.get(k) != v
            }
            if not differing:
                result.log.append(
                    Audit(
                        action="applied",
                        detail=f"person '{add.xref}' already present and identical; no-op",
                        target_xref=add.xref,
                        actor_user_id=policy.actor_user_id,
                    )
                )
                continue
            # Конфликт: persons_added на существующего xref'а — лечится как
            # модификация полей по policy.
            for field_name, (left, right) in differing.items():
                apply, _new, audit, conflict = _resolve_field_conflict(
                    target_value=left,
                    proposed_before=None,
                    proposed_after=right,
                    policy=policy,
                    target_xref=add.xref,
                    field=field_name,
                )
                result.log.append(audit)
                if conflict is not None:
                    result.skipped.append(conflict)
                if apply:
                    result.applied.append(
                        Change(
                            kind="person_field_updated",
                            xref=add.xref,
                            field=field_name,
                            new_value=right,
                        )
                    )
            continue
        result.applied.append(Change(kind="person_added", xref=add.xref, new_value=add.fields))
        result.log.append(
            Audit(
                action="applied",
                detail=f"person '{add.xref}' added with {len(add.fields)} field(s)",
                target_xref=add.xref,
                actor_user_id=policy.actor_user_id,
            )
        )

    # Phase 3: persons_modified — поле за полем.
    for mod in diff.persons_modified:
        existing = target.persons[mod.target_xref].fields
        for field_name, change in mod.field_changes.items():
            apply, _new, audit, conflict = _resolve_field_conflict(
                target_value=existing.get(field_name),
                proposed_before=change.before,
                proposed_after=change.after,
                policy=policy,
                target_xref=mod.target_xref,
                field=field_name,
            )
            result.log.append(audit)
            if conflict is not None:
                result.skipped.append(conflict)
            if apply:
                result.applied.append(
                    Change(
                        kind="person_field_updated",
                        xref=mod.target_xref,
                        field=field_name,
                        new_value=change.after,
                    )
                )

    # Phase 4: persons_removed — soft-delete. Конфликта здесь нет:
    # удаление не overlap'ит с полями. Идемпотентно.
    for rem in diff.persons_removed:
        result.applied.append(Change(kind="person_removed", xref=rem.target_xref))
        result.log.append(
            Audit(
                action="applied",
                detail=f"person '{rem.target_xref}' soft-deleted",
                target_xref=rem.target_xref,
                actor_user_id=policy.actor_user_id,
            )
        )

    # Phase 5: relations_added — overlap'ом считается ровно дубликат той же
    # связи. На самом деле дубликат — это no-op, не конфликт; конфликт здесь
    # был бы при противоречащей семантике (например, если пытаются добавить
    # parent_child(A,B), но в target уже parent_child(C,B) — но это не наш
    # домен здесь, потому что мы оперируем парами, а не «единственным
    # отцом»). Phase 5.7b: дубликаты no-op'ятся, всё остальное добавляется.
    existing_keys = {_relation_key(r) for r in target.relations}
    for rel_a in diff.relations_added:
        as_record = RelationRecord(
            relation_type=rel_a.relation_type,
            person_a=rel_a.person_a_xref,
            person_b=rel_a.person_b_xref,
        )
        key = _relation_key(as_record)
        if key in existing_keys:
            result.log.append(
                Audit(
                    action="applied",
                    detail=(
                        f"relation {rel_a.relation_type}({rel_a.person_a_xref},"
                        f"{rel_a.person_b_xref}) already present; no-op"
                    ),
                    actor_user_id=policy.actor_user_id,
                )
            )
            continue
        result.applied.append(
            Change(
                kind="relation_added",
                relation_type=rel_a.relation_type,
                person_a_xref=rel_a.person_a_xref,
                person_b_xref=rel_a.person_b_xref,
            )
        )
        result.log.append(
            Audit(
                action="applied",
                detail=(
                    f"relation {rel_a.relation_type}({rel_a.person_a_xref},"
                    f"{rel_a.person_b_xref}) added"
                ),
                actor_user_id=policy.actor_user_id,
            )
        )

    # Phase 6: relations_removed — no-op если связи нет.
    for rel_r in diff.relations_removed:
        as_record = RelationRecord(
            relation_type=rel_r.relation_type,
            person_a=rel_r.person_a_xref,
            person_b=rel_r.person_b_xref,
        )
        key = _relation_key(as_record)
        if key not in existing_keys:
            result.log.append(
                Audit(
                    action="applied",
                    detail=(
                        f"relation {rel_r.relation_type}({rel_r.person_a_xref},"
                        f"{rel_r.person_b_xref}) absent; no-op"
                    ),
                    actor_user_id=policy.actor_user_id,
                )
            )
            continue
        result.applied.append(
            Change(
                kind="relation_removed",
                relation_type=rel_r.relation_type,
                person_a_xref=rel_r.person_a_xref,
                person_b_xref=rel_r.person_b_xref,
            )
        )
        result.log.append(
            Audit(
                action="applied",
                detail=(
                    f"relation {rel_r.relation_type}({rel_r.person_a_xref},"
                    f"{rel_r.person_b_xref}) removed"
                ),
                actor_user_id=policy.actor_user_id,
            )
        )

    return result


# ---------------------------------------------------------------------------
# DB-aware applier
# ---------------------------------------------------------------------------

# Поля Person ORM, которые safe-merge умеет писать. Любое поле в diff'е,
# не входящее в этот набор, попадает в provenance.unknown_fields (не теряется,
# но и не пишется в колонки).
_PERSISTABLE_PERSON_FIELDS: frozenset[str] = frozenset({"sex"})


async def _load_target_snapshot(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
) -> tuple[TreeSnapshot, dict[str, uuid.UUID]]:
    """Прочитать текущее состояние target-дерева в snapshot + xref→id map.

    Возвращает:

    * ``TreeSnapshot`` для apply_diff_pure (gedcom_xref → fields).
    * Словарь ``xref → person_id`` для DB-материализации (нужен resolve'ом
      relation'ов в Family/FamilyChild).

    Soft-deleted персоны из snapshot'а исключены: они «не существуют» с
    точки зрения diff'а, и попытка модификации soft-deleted xref'а
    превратится в missing_anchor.
    """
    # Late import: gedcom-parser не depend'ится на shared-models или
    # sqlalchemy в pyproject.toml. apply_diff_to_session — DB-адаптер,
    # и shared-models должен быть установлен у вызывателя (parser-service).
    # Если impossible — ImportError будет понятным сигналом, и пакет
    # сохранит pure-применимость без DB-стека.
    from shared_models.orm import Family, FamilyChild, Person  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    persons_q = await session.execute(
        select(Person).where(
            Person.tree_id == tree_id,
            Person.deleted_at.is_(None),
        )
    )
    persons_rows = list(persons_q.scalars().all())

    snapshot_persons: dict[str, PersonRecord] = {}
    xref_to_id: dict[str, uuid.UUID] = {}
    id_to_xref: dict[uuid.UUID, str] = {}

    for p in persons_rows:
        if p.gedcom_xref is None:
            # У персоны нет xref'а — она не адресуема из diff'а. Пропускаем.
            continue
        snapshot_persons[p.gedcom_xref] = PersonRecord(
            xref=p.gedcom_xref,
            fields={"sex": p.sex},
        )
        xref_to_id[p.gedcom_xref] = p.id
        id_to_xref[p.id] = p.gedcom_xref

    # Relations: spouse через Family.husband_id/wife_id; parent_child через
    # Family.husband_id/wife_id × FamilyChild.child_person_id.
    family_q = await session.execute(
        select(Family).where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
        )
    )
    families: list[Family] = list(family_q.scalars().all())
    family_id_to_parents: dict[uuid.UUID, list[uuid.UUID]] = {}
    relations: list[RelationRecord] = []
    for f in families:
        parents: list[uuid.UUID] = []
        if f.husband_id is not None:
            parents.append(f.husband_id)
        if f.wife_id is not None:
            parents.append(f.wife_id)
        family_id_to_parents[f.id] = parents
        if f.husband_id is not None and f.wife_id is not None:
            h_xref = id_to_xref.get(f.husband_id)
            w_xref = id_to_xref.get(f.wife_id)
            if h_xref is not None and w_xref is not None:
                relations.append(
                    RelationRecord(
                        relation_type="spouse",
                        person_a=h_xref,
                        person_b=w_xref,
                    )
                )

    if family_id_to_parents:
        fc_q = await session.execute(
            select(FamilyChild).where(FamilyChild.family_id.in_(family_id_to_parents.keys()))
        )
        family_children: list[FamilyChild] = list(fc_q.scalars().all())
    else:
        family_children = []
    for fc in family_children:
        child_xref = id_to_xref.get(fc.child_person_id)
        if child_xref is None:
            continue
        for parent_id in family_id_to_parents.get(fc.family_id, []):
            parent_xref = id_to_xref.get(parent_id)
            if parent_xref is None:
                continue
            relations.append(
                RelationRecord(
                    relation_type="parent_child",
                    person_a=parent_xref,
                    person_b=child_xref,
                )
            )

    return TreeSnapshot(persons=snapshot_persons, relations=relations), xref_to_id


async def _persist_changes(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    changes: list[Change],
    xref_to_id: dict[str, uuid.UUID],
    policy: MergePolicy,
) -> None:
    """Материализовать ``Change`` в БД.

    Должно вызываться внутри уже открытого ``session.begin_nested()``.
    На любом исключении SQLAlchemy откатит savepoint, и видимых записей
    не будет.
    """
    # Late import: см. docstring _load_target_snapshot.
    from shared_models.enums import Sex  # noqa: PLC0415
    from shared_models.orm import Family, FamilyChild, Person  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    now = dt.datetime.now(dt.UTC)
    actor = policy.actor_user_id

    for ch in changes:
        if ch.kind == "person_added":
            new_id = uuid.uuid4()
            fields_map = ch.new_value or {}
            sex_value = fields_map.get("sex", Sex.UNKNOWN.value)
            if sex_value not in {s.value for s in Sex}:
                sex_value = Sex.UNKNOWN.value
            unknown_fields = {
                k: v for k, v in fields_map.items() if k not in _PERSISTABLE_PERSON_FIELDS
            }
            provenance: dict[str, Any] = {
                "source_files": ["safe_merge"],
                "manual_edits": [
                    {
                        "user_id": actor,
                        "ts": now.isoformat(),
                        "action": "safe_merge_person_added",
                    }
                ],
            }
            if unknown_fields:
                provenance["unknown_fields"] = unknown_fields
            person = Person(
                id=new_id,
                tree_id=tree_id,
                gedcom_xref=ch.xref,
                sex=sex_value,
                provenance=provenance,
            )
            session.add(person)
            await session.flush()
            assert ch.xref is not None
            xref_to_id[ch.xref] = new_id
        elif ch.kind == "person_field_updated":
            assert ch.xref is not None
            person_id = xref_to_id.get(ch.xref)
            if person_id is None:
                # Новый person в этом же merge'е (ещё не flush'нутый под xref).
                # Найдём через query.
                row = await session.execute(
                    select(Person).where(
                        Person.tree_id == tree_id,
                        Person.gedcom_xref == ch.xref,
                        Person.deleted_at.is_(None),
                    )
                )
                obj = row.scalar_one_or_none()
                if obj is None:
                    msg = f"person_field_updated for unknown xref '{ch.xref}'"
                    raise RuntimeError(msg)
                xref_to_id[ch.xref] = obj.id
            else:
                obj = await session.get(Person, person_id)
                if obj is None:
                    msg = f"person {person_id} disappeared mid-merge"
                    raise RuntimeError(msg)
            if ch.field == "sex":
                value = ch.new_value
                if value not in {s.value for s in Sex}:
                    value = Sex.UNKNOWN.value
                obj.sex = value
            else:
                # Неперсистируемое поле: складываем в provenance.unknown_fields
                # чтобы не потерять.
                prov = dict(obj.provenance or {})
                unknown = dict(prov.get("unknown_fields", {}))
                unknown[ch.field or ""] = ch.new_value
                prov["unknown_fields"] = unknown
                obj.provenance = prov
            await session.flush()
        elif ch.kind == "person_removed":
            assert ch.xref is not None
            person_id = xref_to_id.get(ch.xref)
            if person_id is None:
                continue
            obj = await session.get(Person, person_id)
            if obj is None:
                continue
            obj.deleted_at = now
            await session.flush()
        elif ch.kind == "relation_added":
            assert ch.person_a_xref is not None
            assert ch.person_b_xref is not None
            a_id = xref_to_id.get(ch.person_a_xref)
            b_id = xref_to_id.get(ch.person_b_xref)
            if a_id is None or b_id is None:
                msg = (
                    f"relation_added references unmapped xref(s) "
                    f"({ch.person_a_xref}={a_id}, {ch.person_b_xref}={b_id})"
                )
                raise RuntimeError(msg)
            if ch.relation_type == "spouse":
                a_obj = await session.get(Person, a_id)
                b_obj = await session.get(Person, b_id)
                if a_obj is None or b_obj is None:
                    msg = "spouse relation persons missing"
                    raise RuntimeError(msg)
                # Назначаем husband/wife по sex'у. Если оба одного sex'а или
                # unknown — кладём в husband/wife по позиционному порядку.
                husband_id, wife_id = a_id, b_id
                if a_obj.sex == Sex.FEMALE.value and b_obj.sex == Sex.MALE.value:
                    husband_id, wife_id = b_id, a_id
                family = Family(
                    id=uuid.uuid4(),
                    tree_id=tree_id,
                    husband_id=husband_id,
                    wife_id=wife_id,
                    provenance={
                        "source_files": ["safe_merge"],
                        "manual_edits": [
                            {
                                "user_id": actor,
                                "ts": now.isoformat(),
                                "action": "safe_merge_spouse_added",
                            }
                        ],
                    },
                )
                session.add(family)
            else:
                # parent_child: создаём stub-Family с одним родителем + FamilyChild.
                parent_obj = await session.get(Person, a_id)
                if parent_obj is None:
                    msg = "parent person missing"
                    raise RuntimeError(msg)
                family = Family(
                    id=uuid.uuid4(),
                    tree_id=tree_id,
                    husband_id=a_id if parent_obj.sex != Sex.FEMALE.value else None,
                    wife_id=a_id if parent_obj.sex == Sex.FEMALE.value else None,
                    provenance={
                        "source_files": ["safe_merge"],
                        "manual_edits": [
                            {
                                "user_id": actor,
                                "ts": now.isoformat(),
                                "action": "safe_merge_parent_child_added",
                            }
                        ],
                    },
                )
                session.add(family)
                await session.flush()
                fc = FamilyChild(
                    id=uuid.uuid4(),
                    family_id=family.id,
                    child_person_id=b_id,
                )
                session.add(fc)
            await session.flush()
        elif ch.kind == "relation_removed":
            # Soft-delete семьи, которая описывает эту связь.
            assert ch.person_a_xref is not None
            assert ch.person_b_xref is not None
            a_id = xref_to_id.get(ch.person_a_xref)
            b_id = xref_to_id.get(ch.person_b_xref)
            if a_id is None or b_id is None:
                continue
            if ch.relation_type == "spouse":
                spouse_q = await session.execute(
                    select(Family).where(
                        Family.tree_id == tree_id,
                        Family.deleted_at.is_(None),
                        (
                            (Family.husband_id == a_id) & (Family.wife_id == b_id)
                            | (Family.husband_id == b_id) & (Family.wife_id == a_id)
                        ),
                    )
                )
                for fam in spouse_q.scalars().all():
                    fam.deleted_at = now
            else:
                pc_q = await session.execute(
                    select(FamilyChild)
                    .join(Family, Family.id == FamilyChild.family_id)
                    .where(
                        Family.tree_id == tree_id,
                        Family.deleted_at.is_(None),
                        (Family.husband_id == a_id) | (Family.wife_id == a_id),
                        FamilyChild.child_person_id == b_id,
                    )
                )
                for pc_row in pc_q.scalars().all():
                    pc_fam = await session.get(Family, pc_row.family_id)
                    if pc_fam is not None:
                        pc_fam.deleted_at = now
            await session.flush()


async def apply_diff_to_session(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    diff: DiffReport,
    policy: MergePolicy,
) -> MergeResult:
    """Загружает target-дерево, прогоняет :func:`apply_diff_pure`, материализует.

    Атомарность: материализация идёт внутри ``session.begin_nested()``
    (savepoint). Если внутри poss'а возникнет любое исключение —
    savepoint откатится, а внешняя транзакция останется в работоспособном
    состоянии для caller'а (например, чтобы записать audit-row).

    При ``aborted=True`` (missing_anchor) DB-операций НЕ происходит вообще:
    ни savepoint не открывается, ни flush'и.
    """
    target, xref_to_id = await _load_target_snapshot(session, tree_id=tree_id)
    plan = apply_diff_pure(target, diff, policy)
    if plan.aborted:
        return plan

    async with session.begin_nested():
        await _persist_changes(
            session,
            tree_id=tree_id,
            changes=plan.applied,
            xref_to_id=xref_to_id,
            policy=policy,
        )
    return plan
