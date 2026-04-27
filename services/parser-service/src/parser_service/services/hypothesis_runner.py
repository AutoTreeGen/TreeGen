"""hypothesis_runner: compute & persist hypotheses (Phase 7.2 Task 3).

Связывает три слоя:

1. **Domain ORM** (shared-models) — Person/Source/Place + Hypothesis/Evidence.
2. **Inference engine** (Phase 7.0+7.1) — pure rules + ``compose_hypothesis()``.
3. **Persistence** — INSERT/UPSERT в hypotheses + hypothesis_evidences с
   canonical-ordered subject_ids для idempotency (см. ADR-0021).

Контракт сервиса:

* `compute_hypothesis(session, tree_id, a_id, b_id, type)` — для одной
  пары: fetch subjects → dict-format → compose → persist.
* `bulk_compute_for_dedup_suggestions(session, tree_id, min_confidence)`
  — поверх Phase 3.4 `dedup_finder`: каждое suggestion становится
  hypothesis row.

Сервис **не мутирует** Person / Source / Place (CLAUDE.md §5):
только READ доменных entities + INSERT/UPDATE в `hypotheses`/
`hypothesis_evidences`. Слияние entities — отдельный flow Phase 4.6.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any

import inference_engine
from inference_engine import (
    HypothesisType as EngineHypothesisType,
)
from inference_engine import (
    compose_hypothesis,
)
from inference_engine.rules import (
    BirthPlaceMatchRule,
    BirthYearMatchRule,
    DnaSegmentRelationshipRule,
    InferenceRule,
    SexConsistencyRule,
    SurnameMatchRule,
)
from shared_models.enums import (
    HypothesisComputedBy,
    HypothesisReviewStatus,
    HypothesisSubjectType,
    HypothesisType,
)
from shared_models.orm import (
    DnaKit,
    DnaMatch,
    Event,
    EventParticipant,
    Hypothesis,
    HypothesisEvidence,
    Name,
    Person,
    Place,
    Source,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parser_service.services.dedup_finder import (
    find_person_duplicates,
    find_place_duplicates,
    find_source_duplicates,
)


def _engine_version() -> str:
    """``inference_engine`` package version из metadata.

    Не полагаемся на ``__version__`` атрибут — в Phase 7.0 его не
    экспортировали, а мы не хотим coupling. importlib.metadata —
    стандартный pattern.
    """
    try:
        return pkg_version("inference-engine")
    except PackageNotFoundError:
        return "0.0.0+unknown"


# ----- Default rule pack для compose_hypothesis -----------------------------
# Phase 7.1 rules + Phase 7.3 DNA rule (ADR-0023). DNA-rule silent если
# `context["dna_evidence"]` отсутствует, поэтому добавление безопасно для
# гипотез без линкованных DnaKit. rules_version хешируется из rule_id'ов,
# поэтому появление DNA rule инвалидирует старые hypothesis-rows и
# триггерит пересчёт при следующем `compute_hypothesis` (см. ADR-0021).

_DEFAULT_RULE_CLASSES = (
    SurnameMatchRule,
    BirthYearMatchRule,
    BirthPlaceMatchRule,
    SexConsistencyRule,
    DnaSegmentRelationshipRule,
)


def _compute_rules_version() -> str:
    """Snapshot версии inference-engine + хеш rule_id'ов.

    Формат: ``"engine=<version>;rules=<sha8>"``. Стабилен пока тот же
    set rule'ов используется. При добавлении новых rule в
    `_DEFAULT_RULE_CLASSES` хеш изменится, и старые гипотезы помечаются
    "stale" (UI Phase 4.6 показывает индикатор).
    """
    rule_ids = sorted(cls.rule_id for cls in _DEFAULT_RULE_CLASSES)
    digest = hashlib.sha256("|".join(rule_ids).encode()).hexdigest()[:8]
    return f"engine={_engine_version()};rules={digest}"


# ----- Subject loading ------------------------------------------------------
#
# Hypothesis может быть про person / source / place (а в будущем и family).
# Конвертируем доменный объект в dict-subjects, который ожидают rules
# Phase 7.1 — ровно те же ключи что у `PersonForMatching` /
# `entity_resolution.places.place_match_score` / etc.


async def _person_to_subject(session: AsyncSession, person_id: uuid.UUID) -> dict[str, Any] | None:
    """Собрать Person + sort_order=0 имя + BIRT/DEAT events в dict.

    Тот же набор полей что используется в Phase 3.4 dedup_finder.
    Возвращает None если персона не найдена (caller должен решить —
    raise или skip).
    """
    person = (
        await session.execute(
            select(Person).where(
                Person.id == person_id,
                Person.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if person is None:
        return None

    name = (
        await session.execute(select(Name).where(Name.person_id == person_id, Name.sort_order == 0))
    ).scalar_one_or_none()

    birth_year: int | None = None
    death_year: int | None = None
    birth_place: str | None = None
    events_res = await session.execute(
        select(Event, Place.canonical_name)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .join(Place, Place.id == Event.place_id, isouter=True)
        .where(
            EventParticipant.person_id == person_id,
            Event.deleted_at.is_(None),
            Event.event_type.in_(("BIRT", "DEAT")),
        )
    )
    for event, place_name in events_res.all():
        year = event.date_start.year if event.date_start else None
        if event.event_type == "BIRT":
            if birth_year is None:
                birth_year = year
            if place_name and birth_place is None:
                birth_place = place_name
        elif event.event_type == "DEAT" and death_year is None:
            death_year = year

    return {
        "id": str(person.id),
        "given": name.given_name if name else None,
        "surname": name.surname if name else None,
        "birth_year": birth_year,
        "death_year": death_year,
        "birth_place": birth_place,
        "sex": person.sex,
    }


async def _source_to_subject(session: AsyncSession, source_id: uuid.UUID) -> dict[str, Any] | None:
    source = (
        await session.execute(
            select(Source).where(
                Source.id == source_id,
                Source.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if source is None:
        return None
    return {
        "id": str(source.id),
        "title": source.title,
        "author": source.author,
    }


async def _place_to_subject(session: AsyncSession, place_id: uuid.UUID) -> dict[str, Any] | None:
    place = (
        await session.execute(
            select(Place).where(
                Place.id == place_id,
                Place.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if place is None:
        return None
    return {
        "id": str(place.id),
        "birth_place": place.canonical_name,  # для place_match_score
    }


_SUBJECT_TYPE_MAP = {
    HypothesisType.SAME_PERSON: HypothesisSubjectType.PERSON,
    HypothesisType.PARENT_CHILD: HypothesisSubjectType.PERSON,
    HypothesisType.SIBLINGS: HypothesisSubjectType.PERSON,
    HypothesisType.MARRIAGE: HypothesisSubjectType.PERSON,
    HypothesisType.DUPLICATE_SOURCE: HypothesisSubjectType.SOURCE,
    HypothesisType.DUPLICATE_PLACE: HypothesisSubjectType.PLACE,
}


# ----- Engine type mapping --------------------------------------------------
# inference_engine.HypothesisType покрывает person-related только. Для
# DUPLICATE_SOURCE / DUPLICATE_PLACE — переиспользуем SAME_PERSON в engine
# (он agnostic к смыслу), но в БД пишем правильный shared-models тип.

_ENGINE_TYPE_FOR_PERSISTENT = {
    HypothesisType.SAME_PERSON: EngineHypothesisType.SAME_PERSON,
    HypothesisType.PARENT_CHILD: EngineHypothesisType.PARENT_CHILD,
    HypothesisType.SIBLINGS: EngineHypothesisType.SIBLINGS,
    HypothesisType.MARRIAGE: EngineHypothesisType.MARRIAGE,
    HypothesisType.DUPLICATE_SOURCE: EngineHypothesisType.SAME_PERSON,
    HypothesisType.DUPLICATE_PLACE: EngineHypothesisType.SAME_PERSON,
}


# ----- Public API -----------------------------------------------------------


async def compute_hypothesis(
    session: AsyncSession,
    tree_id: uuid.UUID,
    subject_a_id: uuid.UUID,
    subject_b_id: uuid.UUID,
    hypothesis_type: HypothesisType,
    *,
    computed_by: HypothesisComputedBy = HypothesisComputedBy.AUTOMATIC,
) -> Hypothesis | None:
    """Compute & persist гипотезу для одной пары subjects.

    Шаги:

    1. Canonicalize ids: меньший UUID первый. Это гарантирует, что
       (a, b) и (b, a) попадают в одну строку UNIQUE-индекса
       ``(tree_id, type, a, b)``.
    2. Fetch subjects из БД (по типу гипотезы).
    3. Convert в dict-формат rule'ов.
    4. compose_hypothesis() с дефолтным набором rules.
    5. UPSERT-логика: если запись уже есть — обновляем score+evidences
       но сохраняем reviewed_status (см. ADR-0021 Idempotency).
    6. Возвращаем persisted ORM-объект (без commit — это caller'а ответственность).

    Returns:
        Persisted ``Hypothesis`` или ``None``, если хотя бы один subject
        не найден в БД (broken FK; вызывающий пусть решает — пропускать
        или фейлиться).
    """
    # 1. Canonical order — меньший UUID первый.
    a_id, b_id = (
        (subject_a_id, subject_b_id)
        if str(subject_a_id) < str(subject_b_id)
        else (subject_b_id, subject_a_id)
    )

    # 2. Fetch subjects.
    subject_type = _SUBJECT_TYPE_MAP[hypothesis_type]
    subject_a = await _fetch_subject(session, subject_type, a_id)
    subject_b = await _fetch_subject(session, subject_type, b_id)
    if subject_a is None or subject_b is None:
        return None

    # 3. DNA-aggregate (Phase 7.3.1, ADR-0023). Грузим только для person-пар:
    # SOURCE / PLACE гипотезы DNA-evidence не имеют. None → DNA rule silent.
    dna_evidence: dict[str, Any] | None = None
    if subject_type is HypothesisSubjectType.PERSON:
        dna_evidence = await _load_dna_aggregate(session, a_id, b_id)

    # 4. Compose с временной регистрацией дефолтных правил.
    engine_type = _ENGINE_TYPE_FOR_PERSISTENT[hypothesis_type]
    in_memory = _compose_with_default_rules(
        engine_type=engine_type,
        subject_a=subject_a,
        subject_b=subject_b,
        hypothesis_type_value=hypothesis_type.value,
        dna_evidence=dna_evidence,
    )

    rules_version = _compute_rules_version()

    # 5. UPSERT.
    existing = (
        await session.execute(
            select(Hypothesis)
            .options(selectinload(Hypothesis.evidences))
            .where(
                Hypothesis.tree_id == tree_id,
                Hypothesis.hypothesis_type == hypothesis_type.value,
                Hypothesis.subject_a_id == a_id,
                Hypothesis.subject_b_id == b_id,
                Hypothesis.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    now = dt.datetime.now(dt.UTC)

    if existing is not None and existing.rules_version == rules_version:
        # Версия совпадает → no-op, возвращаем как есть.
        return existing

    if existing is not None:
        # Версия изменилась → пересчитываем score + evidences.
        # reviewed_status сохраняем (user judgment не теряется).
        existing.composite_score = in_memory.composite_score
        existing.computed_at = now
        existing.computed_by = computed_by.value
        existing.rules_version = rules_version
        # Старые evidences заменяем полностью (cascade orphan-delete).
        existing.evidences.clear()
        for ev in in_memory.evidences:
            existing.evidences.append(_to_orm_evidence(ev))
        await session.flush()
        return existing

    # Insert.
    new_hyp = Hypothesis(
        tree_id=tree_id,
        hypothesis_type=hypothesis_type.value,
        subject_a_type=subject_type.value,
        subject_a_id=a_id,
        subject_b_type=subject_type.value,
        subject_b_id=b_id,
        composite_score=in_memory.composite_score,
        computed_at=now,
        computed_by=computed_by.value,
        rules_version=rules_version,
        reviewed_status=HypothesisReviewStatus.PENDING.value,
        provenance={"engine_version": _engine_version()},
    )
    new_hyp.evidences = [_to_orm_evidence(ev) for ev in in_memory.evidences]
    session.add(new_hyp)
    await session.flush()
    return new_hyp


async def bulk_compute_for_dedup_suggestions(
    session: AsyncSession,
    tree_id: uuid.UUID,
    *,
    min_confidence: float = 0.5,
    computed_by: HypothesisComputedBy = HypothesisComputedBy.AUTOMATIC,
) -> int:
    """Конвертировать каждый dedup_finder suggestion в persisted Hypothesis.

    Использует Phase 3.4 ``find_*_duplicates`` как генератор пар-кандидатов:
    он уже сделал SOUR / PLACE / PERSON dedup-фильтр. Каждое suggestion с
    confidence ≥ ``min_confidence`` превращается в одну Hypothesis row.

    Returns:
        Число fresh insert/update'ов. Существующие hypotheses с тем же
        rules_version пропускаются (idempotent).
    """
    count = 0

    # Persons → SAME_PERSON.
    person_pairs = await find_person_duplicates(session, tree_id, threshold=min_confidence)
    for sug in person_pairs:
        result = await compute_hypothesis(
            session,
            tree_id,
            sug.entity_a_id,
            sug.entity_b_id,
            HypothesisType.SAME_PERSON,
            computed_by=computed_by,
        )
        if result is not None:
            count += 1

    # Sources → DUPLICATE_SOURCE.
    source_pairs = await find_source_duplicates(session, tree_id, threshold=min_confidence)
    for sug in source_pairs:
        result = await compute_hypothesis(
            session,
            tree_id,
            sug.entity_a_id,
            sug.entity_b_id,
            HypothesisType.DUPLICATE_SOURCE,
            computed_by=computed_by,
        )
        if result is not None:
            count += 1

    # Places → DUPLICATE_PLACE.
    place_pairs = await find_place_duplicates(session, tree_id, threshold=min_confidence)
    for sug in place_pairs:
        result = await compute_hypothesis(
            session,
            tree_id,
            sug.entity_a_id,
            sug.entity_b_id,
            HypothesisType.DUPLICATE_PLACE,
            computed_by=computed_by,
        )
        if result is not None:
            count += 1

    return count


# ----- Internals ------------------------------------------------------------


async def _fetch_subject(
    session: AsyncSession,
    subject_type: HypothesisSubjectType,
    subject_id: uuid.UUID,
) -> dict[str, Any] | None:
    if subject_type is HypothesisSubjectType.PERSON:
        return await _person_to_subject(session, subject_id)
    if subject_type is HypothesisSubjectType.SOURCE:
        return await _source_to_subject(session, subject_id)
    if subject_type is HypothesisSubjectType.PLACE:
        return await _place_to_subject(session, subject_id)
    # FAMILY support — Phase 7.x.
    return None


def _compose_with_default_rules(
    *,
    engine_type: EngineHypothesisType,
    subject_a: dict[str, Any],
    subject_b: dict[str, Any],
    hypothesis_type_value: str,
    dna_evidence: dict[str, Any] | None = None,
) -> inference_engine.Hypothesis:
    """compose_hypothesis() с дефолтным набором rules.

    Передаём ``rules=`` явно (не через registry) чтобы не конфликтовать
    с другими caller'ами, которые могли зарегистрировать свой набор.
    ``dna_evidence`` (Phase 7.3.1) попадает в context только если есть
    пара kit↔match — DnaSegmentRelationshipRule сам silent при отсутствии.
    """
    rule_instances: list[InferenceRule] = [cls() for cls in _DEFAULT_RULE_CLASSES]
    context: dict[str, Any] = {"hypothesis_type": hypothesis_type_value}
    if dna_evidence is not None:
        context["dna_evidence"] = dna_evidence
    return compose_hypothesis(
        hypothesis_type=engine_type,
        subject_a=subject_a,
        subject_b=subject_b,
        context=context,
        rules=rule_instances,
    )


async def _load_dna_aggregate(
    session: AsyncSession,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Собрать DNA-aggregate для пары persons (Phase 7.3.1, ADR-0023).

    Ищем DnaMatch в обе стороны: kit-владелец=A → matched_person_id=B,
    либо kit-владелец=B → matched_person_id=A. Если матчей несколько,
    берём с максимальным total_cm — это known limitation, full
    aggregation поедет в Phase 7.4 вместе с DnaSegment table.

    Returns:
        Dict в формате, ожидаемом DnaSegmentRelationshipRule (см. ADR-0023
        §«Вариант A»), либо None если матча нет / total_cm нулевой.
        Caller (compute_hypothesis) при None просто не передаёт ключ
        ``dna_evidence`` в context — rule остаётся silent.
    """
    primary_stmt = (
        select(
            DnaMatch.total_cm,
            DnaMatch.largest_segment_cm,
            DnaMatch.segment_count,
            DnaKit.id.label("kit_id"),
            DnaKit.person_id.label("kit_owner_person_id"),
            DnaKit.ethnicity_population.label("kit_ethnicity"),
        )
        .join(DnaKit, DnaKit.id == DnaMatch.kit_id)
        .where(
            DnaMatch.deleted_at.is_(None),
            DnaKit.deleted_at.is_(None),
            DnaMatch.total_cm.is_not(None),
            or_(
                (DnaKit.person_id == person_a_id) & (DnaMatch.matched_person_id == person_b_id),
                (DnaKit.person_id == person_b_id) & (DnaMatch.matched_person_id == person_a_id),
            ),
        )
        .order_by(DnaMatch.total_cm.desc())
        .limit(1)
    )
    primary = (await session.execute(primary_stmt)).first()
    if primary is None:
        return None

    # Ethnicity второй стороны — если у неё тоже есть kit, иначе "general".
    other_person_id = person_b_id if primary.kit_owner_person_id == person_a_id else person_a_id
    other_kit_stmt = (
        select(DnaKit.id, DnaKit.ethnicity_population)
        .where(
            DnaKit.deleted_at.is_(None),
            DnaKit.person_id == other_person_id,
        )
        .limit(1)
    )
    other_kit = (await session.execute(other_kit_stmt)).first()
    other_ethnicity = other_kit.ethnicity_population if other_kit is not None else "general"
    other_kit_id = str(other_kit.id) if other_kit is not None else None

    ethnicity_a: str
    ethnicity_b: str
    kit_a_id: str | None
    kit_b_id: str | None
    if primary.kit_owner_person_id == person_a_id:
        ethnicity_a, ethnicity_b = primary.kit_ethnicity, other_ethnicity
        kit_a_id, kit_b_id = str(primary.kit_id), other_kit_id
    else:
        ethnicity_a, ethnicity_b = other_ethnicity, primary.kit_ethnicity
        kit_a_id, kit_b_id = other_kit_id, str(primary.kit_id)

    return {
        "total_cm": float(primary.total_cm),
        "longest_segment_cm": float(primary.largest_segment_cm or 0.0),
        "segment_count": int(primary.segment_count or 0),
        "ethnicity_population_a": ethnicity_a,
        "ethnicity_population_b": ethnicity_b,
        "source": "dna_match_list",
        "kit_id_a": kit_a_id,
        "kit_id_b": kit_b_id,
    }


def _to_orm_evidence(ev: inference_engine.Evidence) -> HypothesisEvidence:
    """Конвертировать in-memory Evidence в ORM row (без hypothesis_id —
    SQLAlchemy расставит при flush через relationship)."""
    return HypothesisEvidence(
        rule_id=ev.rule_id,
        direction=ev.direction.value,
        weight=ev.weight,
        observation=ev.observation,
        source_provenance=dict(ev.source_provenance),
    )


__all__ = [
    "bulk_compute_for_dedup_suggestions",
    "compute_hypothesis",
]
