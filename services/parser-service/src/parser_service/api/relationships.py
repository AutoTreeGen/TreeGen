"""Phase 15.1 — relationship-level evidence aggregation (см. ADR-0058).

Endpoint:

* ``GET /trees/{tree_id}/relationships/{kind}/{subject_id}/{object_id}/evidence``
  — собирает sources / citations / hypothesis evidences, относящиеся
  именно к этой связи (не к двум персонам отдельно).

URL — composite key вместо stable ``relationship_id``: текущая схема
не имеет single-table-view для всех видов relationships. Composite ключ
RESTfully ugly, но честно отражает реальность; rethink при появлении
``relationships`` view (см. ADR-0058 §«Когда пересмотреть»).

Privacy: gated через :func:`require_tree_role` на VIEWER+. Permission'ы
точно такие же, как `GET /sources` — relationship evidence —
производное от уже видимых данных.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import enum
import logging
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Path, status
from shared_models import TreeRole
from shared_models.orm import (
    Citation,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Hypothesis,
    HypothesisEvidence,
    Person,
    Source,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.schemas import (
    RelationshipEvidenceConfidence,
    RelationshipEvidenceProvenance,
    RelationshipEvidenceResponse,
    RelationshipEvidenceSource,
    RelationshipReference,
)
from parser_service.services.permissions import require_tree_role

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)


class RelationshipKind(enum.StrEnum):
    """Виды relationships, поддержанные Phase 15.1.

    ``parent_child`` — subject — родитель, object — ребёнок (направленная связь).
    ``spouse`` — оба persons супруги/партнёры в одной Family (симметричная).
    ``sibling`` — оба persons — children одной Family (симметричная).

    GEDCOM-расширения (foster/adopted/step) — Phase 15.x; FamilyChild
    хранит ``relation_type``, но в Phase 15.1 мы ограничиваемся биологическим
    parent-child + spouse + sibling. Прочие типы возвращают 404.
    """

    PARENT_CHILD = "parent_child"
    SPOUSE = "spouse"
    SIBLING = "sibling"


# Mapping Phase 15.1 RelationshipKind → inference-engine HypothesisType.
# Не используем ``shared_models.enums.HypothesisType`` напрямую, чтобы Phase 15.1
# мог жить без зависимости на inference-engine типов; маппинг локализован.
_HYPOTHESIS_TYPE_FOR_KIND: Final[dict[RelationshipKind, str]] = {
    RelationshipKind.PARENT_CHILD: "parent_child",
    RelationshipKind.SPOUSE: "marriage",
    RelationshipKind.SIBLING: "siblings",
}

# GEDCOM event types, релевантные для spouse-evidence (citations on these
# events on the family — direct evidence of the marriage).
_SPOUSE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"MARR", "DIV", "ENGA", "ANUL", "MARC", "MARS"}
)


# ---- Resolver helpers -------------------------------------------------------


async def _resolve_parent_child_family(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
) -> Family | None:
    """Найти Family, в которой parent — husband/wife, child — в FamilyChild.

    Возвращает первую попавшуюся семью (могут быть множественные через
    foster/adopted; Phase 15.1 не различает). ``None`` — relationship не
    существует в данных.
    """
    res = await session.execute(
        select(Family)
        .join(FamilyChild, FamilyChild.family_id == Family.id)
        .where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
            FamilyChild.child_person_id == child_id,
            or_(Family.husband_id == parent_id, Family.wife_id == parent_id),
        )
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _resolve_spouse_family(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> Family | None:
    """Найти Family где {husband_id, wife_id} == {person_a, person_b}."""
    res = await session.execute(
        select(Family).where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
            or_(
                (Family.husband_id == person_a_id) & (Family.wife_id == person_b_id),
                (Family.husband_id == person_b_id) & (Family.wife_id == person_a_id),
            ),
        )
    )
    return res.scalar_one_or_none()


async def _resolve_sibling_families(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> list[Family]:
    """Найти все Family, в которых оба persons — children.

    Может быть несколько (например, sibling половинных через разные браки —
    хотя это уже half-sibling, технически отдельная связь; для Phase 15.1
    мы это не различаем и собираем все).
    """
    a_families_subq = (
        select(FamilyChild.family_id).where(FamilyChild.child_person_id == person_a_id)
    ).subquery()
    b_families_subq = (
        select(FamilyChild.family_id).where(FamilyChild.child_person_id == person_b_id)
    ).subquery()

    res = await session.execute(
        select(Family).where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
            Family.id.in_(select(a_families_subq)),
            Family.id.in_(select(b_families_subq)),
        )
    )
    return list(res.scalars().all())


# ---- Source aggregation -----------------------------------------------------


async def _persons_exist(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_ids: tuple[uuid.UUID, ...],
) -> bool:
    """Все ли persons существуют (и не soft-deleted) в tree."""
    res = await session.execute(
        select(Person.id).where(
            Person.tree_id == tree_id,
            Person.deleted_at.is_(None),
            Person.id.in_(person_ids),
        )
    )
    found = {row[0] for row in res.all()}
    return all(pid in found for pid in person_ids)


async def _citations_for_families(
    session: AsyncSession,
    *,
    family_ids: list[uuid.UUID],
) -> list[tuple[Citation, Source]]:
    """Все citations + их Source для перечисленных family.id'шников.

    Soft-deleted citations и sources исключены.
    """
    if not family_ids:
        return []
    res = await session.execute(
        select(Citation, Source)
        .join(Source, Source.id == Citation.source_id)
        .where(
            Citation.entity_type == "family",
            Citation.entity_id.in_(family_ids),
            Citation.deleted_at.is_(None),
            Source.deleted_at.is_(None),
        )
    )
    return [(cit, src) for cit, src in res.all()]


async def _spouse_event_citations(
    session: AsyncSession,
    *,
    family_ids: list[uuid.UUID],
) -> list[tuple[Citation, Source]]:
    """Citations on MARR/DIV/etc. events whose participant — одна из families.

    Это «второй слой» evidence для spouse: marriage record как событие
    со своей цитатой. Phase 15.1 включает события из ``_SPOUSE_EVENT_TYPES``;
    другие event-types (BIRT/DEAT и т.п.) — не relevant для marriage-claim.
    """
    if not family_ids:
        return []
    event_ids_subq = (
        select(Event.id)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .where(
            EventParticipant.family_id.in_(family_ids),
            Event.event_type.in_(_SPOUSE_EVENT_TYPES),
            Event.deleted_at.is_(None),
        )
    ).subquery()
    res = await session.execute(
        select(Citation, Source)
        .join(Source, Source.id == Citation.source_id)
        .where(
            Citation.entity_type == "event",
            Citation.entity_id.in_(select(event_ids_subq)),
            Citation.deleted_at.is_(None),
            Source.deleted_at.is_(None),
        )
    )
    return [(cit, src) for cit, src in res.all()]


def _to_evidence_source(citation: Citation, source: Source) -> RelationshipEvidenceSource:
    """ORM (Citation, Source) → DTO."""
    return RelationshipEvidenceSource(
        source_id=source.id,
        citation_id=citation.id,
        title=source.title,
        repository=source.repository,
        reliability=citation.quality,
        citation=citation.page_or_section,
        snippet=citation.quoted_text,
        url=source.url,
        added_at=citation.created_at,
    )


# ---- Hypothesis lookup ------------------------------------------------------


async def _find_hypothesis(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    hypothesis_type: str,
    subject_a_id: uuid.UUID,
    subject_b_id: uuid.UUID,
) -> Hypothesis | None:
    """Найти Hypothesis для (tree, type, ordered subjects).

    Hypothesis stored с canonical-ordered subjects (a < b). Lookup пробует
    оба порядка для совместимости.
    """
    res = await session.execute(
        select(Hypothesis).where(
            Hypothesis.tree_id == tree_id,
            Hypothesis.hypothesis_type == hypothesis_type,
            Hypothesis.deleted_at.is_(None),
            or_(
                (Hypothesis.subject_a_id == subject_a_id)
                & (Hypothesis.subject_b_id == subject_b_id),
                (Hypothesis.subject_a_id == subject_b_id)
                & (Hypothesis.subject_b_id == subject_a_id),
            ),
        )
    )
    return res.scalar_one_or_none()


async def _hypothesis_evidences(
    session: AsyncSession,
    *,
    hypothesis_id: uuid.UUID,
) -> list[HypothesisEvidence]:
    """Все evidences гипотезы."""
    res = await session.execute(
        select(HypothesisEvidence).where(HypothesisEvidence.hypothesis_id == hypothesis_id)
    )
    return list(res.scalars().all())


def _hypothesis_evidence_to_dto(ev: HypothesisEvidence) -> RelationshipEvidenceSource:
    """HypothesisEvidence → unified RelationshipEvidenceSource DTO.

    Inference-rule evidence не имеет настоящего Source — мы синтезируем
    pseudo-source из rule_id + observation. UI рендерит таких "rule"
    отдельным значком (см. ADR-0058 §«UI rendering»).
    """
    return RelationshipEvidenceSource(
        source_id=None,
        citation_id=None,
        title=f"Inference rule: {ev.rule_id}",
        repository=None,
        reliability=ev.weight,
        citation=None,
        snippet=ev.observation,
        url=None,
        added_at=ev.created_at,
        kind="inference_rule",
        rule_id=ev.rule_id,
    )


# ---- Endpoint --------------------------------------------------------------


@router.get(
    "/trees/{tree_id}/relationships/{kind}/{subject_id}/{object_id}/evidence",
    response_model=RelationshipEvidenceResponse,
    summary="Aggregate sources + hypothesis evidences for a specific relationship",
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def get_relationship_evidence(
    tree_id: uuid.UUID,
    kind: Annotated[RelationshipKind, Path(...)],
    subject_id: uuid.UUID,
    object_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RelationshipEvidenceResponse:
    """Возвращает supporting + contradicting evidence для relationship.

    Алгоритм:

    1. Резолвим relationship на ORM-объекты (Family / FamilyChild rows).
    2. Если ничего не найдено — 404 (relationship не существует в данных).
    3. Собираем ``Source`` через ``Citation`` на найденных Family / Event'ах.
    4. Ищем Hypothesis того же типа и subjects → её evidences (positive
       идут в supporting, negative в contradicting).
    5. Confidence: ``hypothesis.composite_score`` если есть hypothesis,
       иначе naive_count = supporting / (supporting + contradicting).

    ``subject_id`` / ``object_id`` для PARENT_CHILD — направленные
    (subject = родитель, object = ребёнок). Для SPOUSE / SIBLING —
    симметричные, любой порядок ok.
    """
    if subject_id == object_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subject_id and object_id must differ",
        )
    if not await _persons_exist(
        session,
        tree_id=tree_id,
        person_ids=(subject_id, object_id),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"One or both persons not found in tree {tree_id}",
        )

    families = await _resolve_relationship_families(
        session,
        tree_id=tree_id,
        kind=kind,
        subject_id=subject_id,
        object_id=object_id,
    )
    if not families:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No {kind.value} relationship between {subject_id} and "
                f"{object_id} found in tree {tree_id}"
            ),
        )

    family_ids = [f.id for f in families]
    family_citations = await _citations_for_families(session, family_ids=family_ids)
    spouse_event_citations: list[tuple[Citation, Source]] = []
    if kind is RelationshipKind.SPOUSE:
        spouse_event_citations = await _spouse_event_citations(session, family_ids=family_ids)

    supporting: list[RelationshipEvidenceSource] = [
        _to_evidence_source(cit, src) for cit, src in family_citations + spouse_event_citations
    ]
    contradicting: list[RelationshipEvidenceSource] = []

    hypothesis = await _find_hypothesis(
        session,
        tree_id=tree_id,
        hypothesis_type=_HYPOTHESIS_TYPE_FOR_KIND[kind],
        subject_a_id=subject_id,
        subject_b_id=object_id,
    )
    confidence = _build_confidence(hypothesis, supporting_count=len(supporting))

    if hypothesis is not None:
        for ev in await _hypothesis_evidences(session, hypothesis_id=hypothesis.id):
            dto = _hypothesis_evidence_to_dto(ev)
            if ev.direction == "supports":
                supporting.append(dto)
            elif ev.direction == "contradicts":
                contradicting.append(dto)
            # "neutral" — не показываем как supporting/contradicting,
            # но можно будет вынести в Provenance tab в будущей итерации.
        # Пересчитаем naive_count если упали в fallback после добавления
        # contradicting'ов.
        if confidence.method == "naive_count":
            confidence = _naive_count_confidence(
                supporting_count=len(supporting),
                contradicting_count=len(contradicting),
            )

    provenance = _aggregate_provenance(families)

    _LOG.debug(
        "relationship evidence: tree=%s kind=%s supporting=%d contradicting=%d method=%s",
        tree_id,
        kind.value,
        len(supporting),
        len(contradicting),
        confidence.method,
    )

    return RelationshipEvidenceResponse(
        relationship=RelationshipReference(
            kind=kind.value,
            subject_person_id=subject_id,
            object_person_id=object_id,
        ),
        supporting=supporting,
        contradicting=contradicting,
        confidence=confidence,
        provenance=provenance,
    )


async def _resolve_relationship_families(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    kind: RelationshipKind,
    subject_id: uuid.UUID,
    object_id: uuid.UUID,
) -> list[Family]:
    """Резолв relationship → family ORM rows. Пустой list — relationship не найден."""
    if kind is RelationshipKind.PARENT_CHILD:
        family = await _resolve_parent_child_family(
            session,
            tree_id=tree_id,
            parent_id=subject_id,
            child_id=object_id,
        )
        return [family] if family is not None else []
    if kind is RelationshipKind.SPOUSE:
        family = await _resolve_spouse_family(
            session,
            tree_id=tree_id,
            person_a_id=subject_id,
            person_b_id=object_id,
        )
        return [family] if family is not None else []
    # SIBLING.
    return await _resolve_sibling_families(
        session,
        tree_id=tree_id,
        person_a_id=subject_id,
        person_b_id=object_id,
    )


def _build_confidence(
    hypothesis: Hypothesis | None,
    *,
    supporting_count: int,
) -> RelationshipEvidenceConfidence:
    """Confidence rollup. Hypothesis → bayesian_fusion_v2; иначе naive_count."""
    if hypothesis is not None:
        return RelationshipEvidenceConfidence(
            score=float(hypothesis.composite_score),
            method="bayesian_fusion_v2",
            computed_at=hypothesis.computed_at,
            hypothesis_id=hypothesis.id,
        )
    return _naive_count_confidence(
        supporting_count=supporting_count,
        contradicting_count=0,
    )


def _naive_count_confidence(
    *,
    supporting_count: int,
    contradicting_count: int,
) -> RelationshipEvidenceConfidence:
    """Fallback confidence: supporting / (supporting + contradicting).

    При отсутствии и того, и другого — 0.0. UI должен рендерить такой
    score с явным флагом `method="naive_count"` (низкое доверие).
    """
    total = supporting_count + contradicting_count
    score = supporting_count / total if total > 0 else 0.0
    return RelationshipEvidenceConfidence(
        score=score,
        method="naive_count",
        computed_at=dt.datetime.now(dt.UTC),
        hypothesis_id=None,
    )


def _aggregate_provenance(families: list[Family]) -> RelationshipEvidenceProvenance:
    """Сливает ``Family.provenance`` jsonb всех вошедших families.

    Структура provenance per ADR-0003: ``source_files``, ``import_job_id``,
    ``manual_edits``. Берём union всех source_files, последний import_job_id,
    конкатенацию manual_edits.
    """
    source_files: list[str] = []
    manual_edits: list[dict[str, Any]] = []
    import_job_id: uuid.UUID | None = None

    for family in families:
        prov = family.provenance if isinstance(family.provenance, dict) else {}
        files = prov.get("source_files")
        if isinstance(files, list):
            for entry in files:
                if isinstance(entry, str) and entry not in source_files:
                    source_files.append(entry)
        edits = prov.get("manual_edits")
        if isinstance(edits, list):
            for entry in edits:
                if isinstance(entry, dict):
                    manual_edits.append(entry)
        raw_job_id = prov.get("import_job_id")
        if isinstance(raw_job_id, str):
            with contextlib.suppress(ValueError):
                import_job_id = uuid.UUID(raw_job_id)

    return RelationshipEvidenceProvenance(
        source_files=source_files,
        import_job_id=import_job_id,
        manual_edits=manual_edits,
    )
