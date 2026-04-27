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
    Event,
    EventParticipant,
    Hypothesis,
    HypothesisEvidence,
    Name,
    Person,
    Place,
    Source,
    Tree,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parser_service.services.dedup_finder import (
    find_person_duplicates,
    find_place_duplicates,
    find_source_duplicates,
)
from parser_service.services.metrics import (
    hypothesis_compute_duration_seconds,
    hypothesis_created_total,
)
from parser_service.services.notifications import notify_hypothesis_pending_review


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
# Тут — Phase 7.1 rules. Когда appear новые rules (DNA segment, parent-age),
# добавляем сюда. rules_version хешируется из этого набора, чтобы
# reproducibility работал.

_DEFAULT_RULE_CLASSES = (
    SurnameMatchRule,
    BirthYearMatchRule,
    BirthPlaceMatchRule,
    SexConsistencyRule,
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

    # 3+4. Compose с временной регистрацией дефолтных правил.
    # Phase 9.0: timing вокруг compose даёт P95 latency композиции;
    # rule_id="compose_default" — синтетический label (per-rule timing
    # потребовал бы wrap каждого InferenceRule, сейчас не оправдано).
    engine_type = _ENGINE_TYPE_FOR_PERSISTENT[hypothesis_type]
    with hypothesis_compute_duration_seconds.labels(rule_id="compose_default").time():
        in_memory = _compose_with_default_rules(
            engine_type=engine_type,
            subject_a=subject_a,
            subject_b=subject_b,
            hypothesis_type_value=hypothesis_type.value,
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

    # Phase 9.0: counter инкрементится ТОЛЬКО на свежий insert (см. UPSERT
    # branch выше — refresh existing → не считаем как новую гипотезу).
    # rule_id label = hypothesis_type, чтобы отделять SAME_PERSON-create
    # от DUPLICATE_SOURCE-create в Grafana.
    hypothesis_created_total.labels(
        rule_id=hypothesis_type.value,
        tree_id=str(tree_id),
    ).inc()

    # Phase 4.9: light notification — оповещаем tree-owner'а о новой
    # pending-review гипотезе. Fire-and-forget: ошибки/недоступность
    # notification-service'а логируются, но не блокируют persist.
    # Уведомления отправляем только на свежий INSERT — re-compute с
    # новой rules_version — silent (юзер уже видел исходную гипотезу).
    owner_id = await session.scalar(select(Tree.owner_user_id).where(Tree.id == tree_id))
    if owner_id is not None:
        await notify_hypothesis_pending_review(
            user_id=owner_id,
            hypothesis_id=new_hyp.id,
            tree_id=tree_id,
            composite_score=in_memory.composite_score,
            hypothesis_type=hypothesis_type.value,
        )
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
) -> inference_engine.Hypothesis:
    """compose_hypothesis() с дефолтным набором rules Phase 7.1.

    Передаём ``rules=`` явно (не через registry) чтобы не конфликтовать
    с другими caller'ами, которые могли зарегистрировать свой набор.
    """
    rule_instances: list[InferenceRule] = [cls() for cls in _DEFAULT_RULE_CLASSES]
    return compose_hypothesis(
        hypothesis_type=engine_type,
        subject_a=subject_a,
        subject_b=subject_b,
        context={"hypothesis_type": hypothesis_type_value},
        rules=rule_instances,
    )


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
