"""Person merger — manual `preview → commit → undo` flow (Phase 4.6, ADR-0022).

CLAUDE.md §5 invariant — auto-merge запрещён. Этот модуль не вызывается
без explicit `confirm:true` в API payload (см. ``api/persons.py``). Сам
``apply_merge`` транзакционно идемпотентен через ``confirm_token``.

Сервис трогает следующие таблицы внутри одной транзакции коммита:

* ``persons``         — UPDATE survivor (provenance/version), UPDATE merged
                        (merged_into_person_id, deleted_at, status).
* ``names``           — REPARENT с merged.id на survivor.id, sort_order
                        смещается на ``+1000``.
* ``events`` /
  ``event_participants`` — REPARENT participants на survivor; коллапс
                        дубликатов по ключу
                        ``(event_type, date_start, place_id, custom_type)``.
* ``families`` /
  ``family_children`` — REPARENT husband_id / wife_id / child_person_id.
* ``person_merge_logs`` — INSERT новой строки с ``dry_run_diff_json``
                        (или 200 идемпотентно при том же токене).

Hypothesis ORM (из Phase 7.2) **не мутируется** — только проверяются
конфликты в ``check_hypothesis_conflicts``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from shared_models.enums import EntityStatus, HypothesisReviewStatus, HypothesisType
from shared_models.orm import (
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Hypothesis,
    Name,
    Person,
    PersonMergeLog,
)
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

UNDO_WINDOW_DAYS = 90
NAME_SORT_ORDER_OFFSET = 1000

SurvivorChoice = Literal["left", "right"]
ConflictReason = Literal[
    "rejected_same_person",
    "subject_already_merged",
    "cross_relationship_conflict",
]


# -----------------------------------------------------------------------------
# Public dataclasses (вход / выход для API-слоя)
# -----------------------------------------------------------------------------


@dataclass
class HypothesisConflict:
    """Один конфликт, блокирующий merge до его разрешения."""

    reason: ConflictReason
    hypothesis_id: uuid.UUID | None
    detail: str


@dataclass
class FieldDiff:
    """Изменение одного скалярного поля персоны после merge'а."""

    field: str
    survivor_value: Any
    merged_value: Any
    after_merge_value: Any


@dataclass
class EventDiff:
    """Что произойдёт с одним событием при коллапсе."""

    event_id: uuid.UUID
    action: Literal["reparent", "collapse_into_survivor", "keep_separate"]
    collapsed_into: uuid.UUID | None  # event_id survivor'а если collapse


@dataclass
class NameDiff:
    """Имя merged'а получает offset в sort_order и переподключается."""

    name_id: uuid.UUID
    old_sort_order: int
    new_sort_order: int


@dataclass
class FamilyMembershipDiff:
    """Какие family-FK переключатся с merged на survivor."""

    table: Literal["families.husband_id", "families.wife_id", "family_children.child_person_id"]
    row_id: uuid.UUID  # family.id или family_children.id


@dataclass
class MergeDiff:
    """Полный snapshot изменений, который вернёт preview.

    Этот dataclass сериализуется в `person_merge_logs.dry_run_diff_json` и
    становится «истиной» для UI history view + undo.
    """

    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    default_survivor_id: uuid.UUID
    fields: list[FieldDiff] = field(default_factory=list)
    names: list[NameDiff] = field(default_factory=list)
    events: list[EventDiff] = field(default_factory=list)
    family_memberships: list[FamilyMembershipDiff] = field(default_factory=list)
    hypothesis_check: Literal[
        "no_hypotheses_found",
        "no_conflicts",
        "conflicts_blocking",
    ] = "no_hypotheses_found"
    conflicts: list[HypothesisConflict] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Internal helpers — survivor selection
# -----------------------------------------------------------------------------


def _provenance_source_count(person: Person) -> int:
    """Сколько ``source_files`` у персоны в provenance — поле может
    отсутствовать (старые импорты), интерпретируем как 0."""
    prov = person.provenance or {}
    files = prov.get("source_files")
    return len(files) if isinstance(files, list) else 0


def _pick_default_survivor(a: Person, b: Person) -> uuid.UUID:
    """Дефолт по ADR-0022 §Survivor selection."""
    a_count = _provenance_source_count(a)
    b_count = _provenance_source_count(b)
    if a_count != b_count:
        winner = a if a_count > b_count else b
    elif a.confidence_score != b.confidence_score:
        winner = a if a.confidence_score > b.confidence_score else b
    else:
        winner = a if a.created_at <= b.created_at else b
    # `cast` нужен для pre-commit mypy isolated env (без sqlalchemy[mypy]
    # plugin'а Mapped[uuid.UUID] виден как Any).
    return cast("uuid.UUID", winner.id)


def _resolve_survivor_merged(
    a: Person,
    b: Person,
    survivor_choice: SurvivorChoice | None,
) -> tuple[Person, Person, uuid.UUID]:
    """Из двух персон + выбора UI получаем (survivor, merged, default_id)."""
    default_id = _pick_default_survivor(a, b)
    if survivor_choice == "left":
        return a, b, default_id
    if survivor_choice == "right":
        return b, a, default_id
    # None → используем default
    if default_id == a.id:
        return a, b, default_id
    return b, a, default_id


# -----------------------------------------------------------------------------
# Hypothesis conflicts
# -----------------------------------------------------------------------------


async def check_hypothesis_conflicts(
    session: AsyncSession,
    a: Person,
    b: Person,
) -> tuple[
    list[HypothesisConflict], Literal["no_hypotheses_found", "no_conflicts", "conflicts_blocking"]
]:
    """Возвращает список конфликтов и общий статус проверки.

    1. ``rejected_same_person`` — пользователь раньше явно сказал
       «это не дубликат» (Hypothesis с reviewed_status='rejected'
       для same_person этой пары).
    2. ``subject_already_merged`` — у одной из персон уже стоит
       ``merged_into_person_id`` или ``deleted_at``.
    3. ``cross_relationship_conflict`` — обе персоны субъекты двух
       противоречивых гипотез про одну и ту же другую сущность
       (одна `confirmed`, другая `rejected`); merge соберёт оба
       evidence'а у одного survivor'а.

    Если в Hypothesis ORM нет ни одной строки про этих двух — статус
    ``no_hypotheses_found`` (degrade gracefully).
    """
    conflicts: list[HypothesisConflict] = []

    # (2) Уже-merged субъект блокируем сразу.
    for person in (a, b):
        if person.merged_into_person_id is not None or person.deleted_at is not None:
            conflicts.append(
                HypothesisConflict(
                    reason="subject_already_merged",
                    hypothesis_id=None,
                    detail=(
                        f"Person {person.id} is already merged or deleted "
                        f"(merged_into={person.merged_into_person_id}, "
                        f"deleted_at={person.deleted_at})."
                    ),
                )
            )

    pair_ids = (a.id, b.id)
    res = await session.execute(
        select(Hypothesis).where(
            Hypothesis.tree_id == a.tree_id,
            Hypothesis.deleted_at.is_(None),
            or_(
                Hypothesis.subject_a_id.in_(pair_ids),
                Hypothesis.subject_b_id.in_(pair_ids),
            ),
        )
    )
    related = list(res.scalars().all())
    if not related and not conflicts:
        return [], "no_hypotheses_found"

    # (1) rejected same_person для этой пары (в любом порядке).
    pair_set = set(pair_ids)
    for hyp in related:
        if (
            hyp.hypothesis_type == HypothesisType.SAME_PERSON.value
            and hyp.reviewed_status == HypothesisReviewStatus.REJECTED.value
            and {hyp.subject_a_id, hyp.subject_b_id} == pair_set
        ):
            conflicts.append(
                HypothesisConflict(
                    reason="rejected_same_person",
                    hypothesis_id=hyp.id,
                    detail=(
                        "User previously rejected the same_person hypothesis "
                        "for this pair. Resolve hypothesis review first."
                    ),
                )
            )

    # (3) Cross-relationship conflict: одна и та же связь между одним
    # из {a, b} и третьим X помечена и confirmed, и rejected. После
    # merge'а оба evidence'а перейдут на survivor'а — мы не можем
    # одновременно держать «X — отец survivor'а» и «X — не отец».
    # Группируем гипотезы по (other_subject, hypothesis_type).
    grouped: dict[tuple[uuid.UUID, str], dict[str, list[uuid.UUID]]] = {}
    for hyp in related:
        # Для same_person этой пары не делаем — обрабатывается в (1).
        if {hyp.subject_a_id, hyp.subject_b_id} == pair_set:
            continue
        # Для каждой гипотезы определяем «другую сторону» (не из {a, b}).
        if hyp.subject_a_id in pair_set:
            other = hyp.subject_b_id
        elif hyp.subject_b_id in pair_set:
            other = hyp.subject_a_id
        else:
            continue
        key = (other, hyp.hypothesis_type)
        bucket = grouped.setdefault(key, {"confirmed": [], "rejected": []})
        if hyp.reviewed_status == HypothesisReviewStatus.CONFIRMED.value:
            bucket["confirmed"].append(hyp.id)
        elif hyp.reviewed_status == HypothesisReviewStatus.REJECTED.value:
            bucket["rejected"].append(hyp.id)

    for (other, hyp_type), buckets in grouped.items():
        if buckets["confirmed"] and buckets["rejected"]:
            conflicts.append(
                HypothesisConflict(
                    reason="cross_relationship_conflict",
                    hypothesis_id=buckets["confirmed"][0],
                    detail=(
                        f"Conflicting `{hyp_type}` hypotheses about other subject "
                        f"{other}: confirmed and rejected — merge would unify "
                        "both evidence chains on the survivor. Resolve first."
                    ),
                )
            )

    status: Literal["no_hypotheses_found", "no_conflicts", "conflicts_blocking"] = (
        "conflicts_blocking" if conflicts else "no_conflicts"
    )
    return conflicts, status


# -----------------------------------------------------------------------------
# Diff computation (preview)
# -----------------------------------------------------------------------------


async def compute_diff(
    session: AsyncSession,
    a_id: uuid.UUID,
    b_id: uuid.UUID,
    survivor_choice: SurvivorChoice | None = None,
) -> MergeDiff:
    """Детерминированный preview merge'а.

    Никаких UPDATE / INSERT — только SELECT'ы. Возвращает полную
    структуру изменений + результат hypothesis-check'а. Если check
    нашёл блокирующие конфликты — поле ``conflicts`` непустое; вызывающий
    (api-слой) преобразует это в 409.
    """
    pair = await _load_persons(session, (a_id, b_id))
    if a_id not in pair or b_id not in pair:
        missing = {a_id, b_id} - pair.keys()
        msg = f"Persons not found or deleted: {missing}"
        raise PersonMergerLookupError(msg)
    # переменная `pair` без NULL-полей — берём прямые ссылки на ORM.

    a, b = pair[a_id], pair[b_id]
    survivor, merged, default_survivor_id = _resolve_survivor_merged(a, b, survivor_choice)

    diff = MergeDiff(
        survivor_id=survivor.id,
        merged_id=merged.id,
        default_survivor_id=default_survivor_id,
    )

    # Field-level — статичные поля Person, которые меняются.
    diff.fields = [
        FieldDiff(
            field="confidence_score",
            survivor_value=survivor.confidence_score,
            merged_value=merged.confidence_score,
            after_merge_value=max(survivor.confidence_score, merged.confidence_score),
        ),
        FieldDiff(
            field="version_id",
            survivor_value=survivor.version_id,
            merged_value=merged.version_id,
            after_merge_value=survivor.version_id + 1,
        ),
        FieldDiff(
            field="merged_into_person_id",
            survivor_value=None,
            merged_value=None,
            after_merge_value=str(survivor.id),
        ),
        FieldDiff(
            field="deleted_at",
            survivor_value=None,
            merged_value=None,
            after_merge_value="<now()>",
        ),
        FieldDiff(
            field="status",
            survivor_value=survivor.status,
            merged_value=merged.status,
            after_merge_value=EntityStatus.MERGED.value,
        ),
    ]

    # Имена merged'а: будут переподключены с offset +1000.
    names_res = await session.execute(
        select(Name).where(Name.person_id == merged.id).order_by(Name.sort_order)
    )
    diff.names = [
        NameDiff(
            name_id=name.id,
            old_sort_order=name.sort_order,
            new_sort_order=name.sort_order + NAME_SORT_ORDER_OFFSET,
        )
        for name in names_res.scalars().all()
    ]

    # Events: загружаем оба набора и считаем коллапсы.
    events_by_person = await _load_events(session, (survivor.id, merged.id))
    survivor_keys = {_event_key(e): e for e in events_by_person[survivor.id]}
    for event in events_by_person[merged.id]:
        key = _event_key(event)
        if key in survivor_keys:
            diff.events.append(
                EventDiff(
                    event_id=event.id,
                    action="collapse_into_survivor",
                    collapsed_into=survivor_keys[key].id,
                )
            )
        else:
            diff.events.append(
                EventDiff(
                    event_id=event.id,
                    action="reparent",
                    collapsed_into=None,
                )
            )

    # Family memberships: husband_id / wife_id / family_children.
    fam_res = await session.execute(
        select(Family).where(or_(Family.husband_id == merged.id, Family.wife_id == merged.id))
    )
    for fam in fam_res.scalars().all():
        if fam.husband_id == merged.id:
            diff.family_memberships.append(
                FamilyMembershipDiff(table="families.husband_id", row_id=fam.id)
            )
        if fam.wife_id == merged.id:
            diff.family_memberships.append(
                FamilyMembershipDiff(table="families.wife_id", row_id=fam.id)
            )

    children_res = await session.execute(
        select(FamilyChild).where(FamilyChild.child_person_id == merged.id)
    )
    for fc in children_res.scalars().all():
        diff.family_memberships.append(
            FamilyMembershipDiff(table="family_children.child_person_id", row_id=fc.id)
        )

    # Hypothesis-check.
    conflicts, status = await check_hypothesis_conflicts(session, a, b)
    diff.conflicts = conflicts
    diff.hypothesis_check = status

    return diff


# -----------------------------------------------------------------------------
# Apply (commit)
# -----------------------------------------------------------------------------


async def apply_merge(
    session: AsyncSession,
    a_id: uuid.UUID,
    b_id: uuid.UUID,
    survivor_choice: SurvivorChoice | None,
    confirm_token: str,
    *,
    merged_by_user_id: uuid.UUID | None = None,
) -> PersonMergeLog:
    """Транзакционно выполняет merge.

    Идемпотентность:
        Повторный вызов с тем же ``confirm_token`` для той же пары и того
        же survivor'а возвращает существующий ``PersonMergeLog`` без
        повторного коммита (через partial-уникальный индекс
        ``uq_person_merge_logs_active``).

    Конфликты:
        Если ``check_hypothesis_conflicts`` находит блокирующие конфликты —
        бросает ``MergeBlockedError`` (caller возвращает 409).

    Транзакция:
        Caller обязан вызывать внутри `async with session.begin()`. Этот
        метод сам не коммитит — позволяет API-слою решить о rollback'е.
    """
    # Сначала проверяем идемпотентность ПО ТОКЕНУ — до compute_diff,
    # потому что после первого merge'а одна из персон уже soft-deleted'на
    # и conflict-gate (subject_already_merged) её отклонит. Активный лог
    # с тем же token'ом + парой (в любом порядке) — это retry того же
    # запроса, отдаём существующую строку.
    existing_by_token = await session.execute(
        select(PersonMergeLog).where(
            PersonMergeLog.confirm_token == confirm_token,
            PersonMergeLog.undone_at.is_(None),
            PersonMergeLog.purged_at.is_(None),
        )
    )
    for prior_log in existing_by_token.scalars().all():
        if {prior_log.survivor_id, prior_log.merged_id} == {a_id, b_id}:
            return cast("PersonMergeLog", prior_log)

    diff = await compute_diff(session, a_id, b_id, survivor_choice)
    if diff.conflicts:
        raise MergeBlockedError(diff.conflicts)

    # Загружаем survivor + merged один раз (compute_diff уже подгружал,
    # но сейчас нужны mutable instances + tree_id для идемпотентности).
    pair = await _load_persons(session, (diff.survivor_id, diff.merged_id))
    survivor = pair[diff.survivor_id]
    merged = pair[diff.merged_id]

    # 1) Имена merged'а: смещаем sort_order, переподключаем на survivor.
    for name_diff in diff.names:
        await session.execute(
            update(Name)
            .where(Name.id == name_diff.name_id)
            .values(person_id=survivor.id, sort_order=name_diff.new_sort_order)
        )

    # 2) Family memberships: переключаем FK с merged на survivor.
    for fm in diff.family_memberships:
        if fm.table == "families.husband_id":
            await session.execute(
                update(Family).where(Family.id == fm.row_id).values(husband_id=survivor.id)
            )
        elif fm.table == "families.wife_id":
            await session.execute(
                update(Family).where(Family.id == fm.row_id).values(wife_id=survivor.id)
            )
        else:
            await session.execute(
                update(FamilyChild)
                .where(FamilyChild.id == fm.row_id)
                .values(child_person_id=survivor.id)
            )

    # 3) Events: коллапс vs reparent.
    # Для коллапса: удаляем merged-event (CASCADE убирает participant'а).
    # Для reparent: переподключаем event_participant на survivor.id.
    collapse_event_ids = [
        ed.event_id for ed in diff.events if ed.action == "collapse_into_survivor"
    ]
    reparent_event_ids = [ed.event_id for ed in diff.events if ed.action == "reparent"]

    if reparent_event_ids:
        await session.execute(
            update(EventParticipant)
            .where(
                EventParticipant.person_id == merged.id,
                EventParticipant.event_id.in_(reparent_event_ids),
            )
            .values(person_id=survivor.id)
        )
    if collapse_event_ids:
        # Удаляем сами event-row у merged — CASCADE уберёт participant'ов.
        await session.execute(delete(Event).where(Event.id.in_(collapse_event_ids)))

    # 4) Persons: survivor поглощает provenance / score / version,
    # merged получает merged_into / deleted_at / status=MERGED.
    new_provenance = dict(survivor.provenance or {})
    merged_xrefs = list(new_provenance.setdefault("merged_xrefs", []))
    if merged.gedcom_xref:
        merged_xrefs.append(merged.gedcom_xref)
    new_provenance["merged_xrefs"] = merged_xrefs
    merged_from = list(new_provenance.setdefault("merged_from", []))
    merged_from.append(str(merged.id))
    new_provenance["merged_from"] = merged_from
    # source_files объединяем set'ом (sorted), чтобы был детерминированный
    # порядок и round-trip diff JSON.
    survivor_sources = list((survivor.provenance or {}).get("source_files") or [])
    merged_sources = list((merged.provenance or {}).get("source_files") or [])
    union_sources = sorted(set(survivor_sources) | set(merged_sources))
    if union_sources:
        new_provenance["source_files"] = union_sources

    new_score = max(survivor.confidence_score, merged.confidence_score)

    survivor.provenance = new_provenance
    survivor.confidence_score = new_score
    # version_id будет автоинкрементирован event listener'ом, но явно
    # выставим для теста (некоторые конфиги отключают listener).
    survivor.version_id = survivor.version_id + 1

    now = dt.datetime.now(dt.UTC)
    merged.merged_into_person_id = survivor.id
    merged.deleted_at = now
    merged.status = EntityStatus.MERGED.value

    # 5) Persist person_merge_logs row.
    log = PersonMergeLog(
        tree_id=survivor.tree_id,
        survivor_id=survivor.id,
        merged_id=merged.id,
        merged_at=now,
        merged_by_user_id=merged_by_user_id,
        confirm_token=confirm_token,
        dry_run_diff_json=_diff_to_jsonb(diff),
    )
    session.add(log)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Race: другой запрос только что вставил тот же (token, pair).
        # Ловим уникальность partial-индекса и возвращаем существующую
        # строку — это правильный idempotent-ответ для retry-сценария.
        await session.rollback()
        existing_after_race = await session.execute(
            select(PersonMergeLog).where(
                PersonMergeLog.tree_id == survivor.tree_id,
                PersonMergeLog.survivor_id == survivor.id,
                PersonMergeLog.merged_id == merged.id,
                PersonMergeLog.confirm_token == confirm_token,
                PersonMergeLog.undone_at.is_(None),
                PersonMergeLog.purged_at.is_(None),
            )
        )
        existing_log = existing_after_race.scalar_one_or_none()
        if existing_log is not None:
            return cast("PersonMergeLog", existing_log)
        msg = "Race during apply_merge"
        raise PersonMergerError(msg) from exc

    return log


# -----------------------------------------------------------------------------
# Undo
# -----------------------------------------------------------------------------


async def undo_merge(
    session: AsyncSession,
    merge_id: uuid.UUID,
    *,
    undone_by_user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> PersonMergeLog:
    """Откатывает merge, если в окне 90 дней и merged person ещё жив."""
    res = await session.execute(select(PersonMergeLog).where(PersonMergeLog.id == merge_id))
    log = res.scalar_one_or_none()
    if log is None:
        msg = f"Merge log {merge_id} not found"
        raise PersonMergerLookupError(msg)
    if log.undone_at is not None:
        reason = "already_undone"
        detail = f"Merge {merge_id} already undone at {log.undone_at}"
        raise UndoNotAllowedError(reason, detail)
    if log.purged_at is not None:
        reason = "merged_person_purged"
        detail = f"Merged person was hard-deleted at {log.purged_at}; undo impossible"
        raise UndoNotAllowedError(reason, detail)

    current_time = now or dt.datetime.now(dt.UTC)
    if (current_time - log.merged_at).days >= UNDO_WINDOW_DAYS:
        reason = "undo_window_expired"
        detail = f"Undo window of {UNDO_WINDOW_DAYS} days expired (merged_at={log.merged_at})."
        raise UndoNotAllowedError(reason, detail)

    # Проверяем что merged person всё ещё в БД (soft-deleted, но не hard).
    merged_res = await session.execute(select(Person).where(Person.id == log.merged_id))
    merged = merged_res.scalar_one_or_none()
    if merged is None:
        reason = "merged_person_purged"
        detail = f"Merged person {log.merged_id} no longer exists in database"
        raise UndoNotAllowedError(reason, detail)
    survivor_res = await session.execute(select(Person).where(Person.id == log.survivor_id))
    survivor = survivor_res.scalar_one_or_none()
    if survivor is None:
        reason = "survivor_purged"
        detail = f"Survivor {log.survivor_id} no longer exists; cannot reverse"
        raise UndoNotAllowedError(reason, detail)

    diff_json = log.dry_run_diff_json
    diff = _diff_from_jsonb(diff_json)

    # 1) Persons: переключаем merged обратно в active, survivor.version_id += 1.
    merged.merged_into_person_id = None
    merged.deleted_at = None
    merged.status = EntityStatus.PROBABLE.value
    survivor.version_id = survivor.version_id + 1
    # Снимаем merged_xrefs / merged_from записи, добавленные этим merge'ем.
    new_prov = dict(survivor.provenance or {})
    if "merged_from" in new_prov:
        new_prov["merged_from"] = [x for x in new_prov["merged_from"] if x != str(log.merged_id)]
        if not new_prov["merged_from"]:
            new_prov.pop("merged_from")
    if "merged_xrefs" in new_prov and merged.gedcom_xref:
        new_prov["merged_xrefs"] = [x for x in new_prov["merged_xrefs"] if x != merged.gedcom_xref]
        if not new_prov["merged_xrefs"]:
            new_prov.pop("merged_xrefs")
    survivor.provenance = new_prov

    # 2) Имена: возвращаем sort_order и person_id.
    for name_d in diff.names:
        await session.execute(
            update(Name)
            .where(Name.id == name_d.name_id)
            .values(person_id=merged.id, sort_order=name_d.old_sort_order)
        )

    # 3) Family memberships: возвращаем FK на merged.id.
    for fm in diff.family_memberships:
        if fm.table == "families.husband_id":
            await session.execute(
                update(Family).where(Family.id == fm.row_id).values(husband_id=merged.id)
            )
        elif fm.table == "families.wife_id":
            await session.execute(
                update(Family).where(Family.id == fm.row_id).values(wife_id=merged.id)
            )
        else:
            await session.execute(
                update(FamilyChild)
                .where(FamilyChild.id == fm.row_id)
                .values(child_person_id=merged.id)
            )

    # 4) Events: reparent → переключаем event_participants обратно
    # с survivor.id на merged.id для тех event_id, что были «reparent».
    # Collapse-events были hard-delete'нуты при merge — восстановить
    # их без полного snapshot контекста невозможно. ADR-0022 §Undo
    # явно говорит: collapsed events «восстанавливаются как отдельные
    # у merged'а если были uniquely свои». В первой итерации
    # ограничиваемся только reparent-events; collapsed остаются у
    # survivor'а (TODO Phase 4.6.1 — точное восстановление).
    # diff.events уже содержат UUID после _diff_from_jsonb.
    reparent_event_ids = [ed.event_id for ed in diff.events if ed.action == "reparent"]
    if reparent_event_ids:
        await session.execute(
            update(EventParticipant)
            .where(
                EventParticipant.person_id == survivor.id,
                EventParticipant.event_id.in_(reparent_event_ids),
            )
            .values(person_id=merged.id)
        )

    log.undone_at = current_time
    log.undone_by_user_id = undone_by_user_id
    await session.flush()
    return cast("PersonMergeLog", log)


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------


class PersonMergerError(Exception):
    """Базовый класс ошибок сервиса."""


class PersonMergerLookupError(PersonMergerError):
    """Person или merge_log не найдены."""


class MergeBlockedError(PersonMergerError):
    """Hypothesis-conflict gate сработал — merge не выполнен."""

    def __init__(self, conflicts: list[HypothesisConflict]) -> None:
        super().__init__(f"Merge blocked by {len(conflicts)} conflict(s)")
        self.conflicts = conflicts


class UndoNotAllowedError(PersonMergerError):
    """Окно undo истекло, merged person purged, или undo уже выполнен."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


async def _load_persons(
    session: AsyncSession,
    ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, Person]:
    """Загружает Person'ов по списку id (включая soft-deleted, нужно для undo)."""
    res = await session.execute(select(Person).where(Person.id.in_(ids)))
    return {p.id: p for p in res.scalars().all()}


async def _load_events(
    session: AsyncSession,
    person_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, list[Event]]:
    """Возвращает {person_id: [Event]} через event_participants."""
    res = await session.execute(
        select(EventParticipant.person_id, Event)
        .join(Event, Event.id == EventParticipant.event_id)
        .where(
            EventParticipant.person_id.in_(person_ids),
            Event.deleted_at.is_(None),
        )
    )
    grouped: dict[uuid.UUID, list[Event]] = {pid: [] for pid in person_ids}
    for person_id, event in res.all():
        grouped[person_id].append(event)
    return grouped


def _event_key(event: Event) -> tuple[str, str | None, str | None, str | None]:
    """Ключ для коллапса дубликатов (см. ADR-0022 §Events)."""
    return (
        event.event_type,
        event.date_start.isoformat() if event.date_start else None,
        str(event.place_id) if event.place_id else None,
        event.custom_type,
    )


def _diff_to_jsonb(diff: MergeDiff) -> dict[str, Any]:
    """Сериализация ``MergeDiff`` в JSON-friendly dict для JSONB-колонки."""
    return {
        "survivor_id": str(diff.survivor_id),
        "merged_id": str(diff.merged_id),
        "default_survivor_id": str(diff.default_survivor_id),
        "fields": [
            {
                "field": f.field,
                "survivor_value": _jsonable(f.survivor_value),
                "merged_value": _jsonable(f.merged_value),
                "after_merge_value": _jsonable(f.after_merge_value),
            }
            for f in diff.fields
        ],
        "names": [
            {
                "name_id": str(n.name_id),
                "old_sort_order": n.old_sort_order,
                "new_sort_order": n.new_sort_order,
            }
            for n in diff.names
        ],
        "events": [
            {
                "event_id": str(e.event_id),
                "action": e.action,
                "collapsed_into": str(e.collapsed_into) if e.collapsed_into else None,
            }
            for e in diff.events
        ],
        "family_memberships": [
            {"table": fm.table, "row_id": str(fm.row_id)} for fm in diff.family_memberships
        ],
        "hypothesis_check": diff.hypothesis_check,
        "conflicts": [
            {
                "reason": c.reason,
                "hypothesis_id": str(c.hypothesis_id) if c.hypothesis_id else None,
                "detail": c.detail,
            }
            for c in diff.conflicts
        ],
    }


def _diff_from_jsonb(payload: dict[str, Any]) -> MergeDiff:
    """Обратная сериализация из JSONB. Для undo мы читаем только то,
    что нам нужно для апплая в обратную сторону."""
    return MergeDiff(
        survivor_id=uuid.UUID(payload["survivor_id"]),
        merged_id=uuid.UUID(payload["merged_id"]),
        default_survivor_id=uuid.UUID(payload["default_survivor_id"]),
        names=[
            NameDiff(
                name_id=uuid.UUID(n["name_id"]),
                old_sort_order=int(n["old_sort_order"]),
                new_sort_order=int(n["new_sort_order"]),
            )
            for n in payload.get("names", [])
        ],
        events=[
            EventDiff(
                event_id=uuid.UUID(e["event_id"]),
                action=e["action"],
                collapsed_into=(
                    uuid.UUID(e["collapsed_into"]) if e.get("collapsed_into") else None
                ),
            )
            for e in payload.get("events", [])
        ],
        family_memberships=[
            FamilyMembershipDiff(table=fm["table"], row_id=uuid.UUID(fm["row_id"]))
            for fm in payload.get("family_memberships", [])
        ],
    )


def _jsonable(value: Any) -> Any:
    """Convert datetimes/UUIDs/etc to JSON-safe primitives."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value
