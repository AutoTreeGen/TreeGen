"""Hypotheses HTTP API (Phase 7.2 Task 4 + Phase 7.5 bulk-compute).

Эндпоинты:

* ``POST /trees/{tree_id}/hypotheses`` — compute & persist гипотезу.
* ``GET /trees/{tree_id}/hypotheses`` — paginated list.
* ``GET /hypotheses/{id}`` — детальный view с evidences[].
* ``PATCH /hypotheses/{id}/review`` — user judgment (NO auto-merge).

Phase 7.5 — bulk-compute job:

* ``POST /trees/{tree_id}/hypotheses/compute-all`` — enqueue + sync execute.
* ``GET /trees/{tree_id}/hypotheses/compute-jobs/{job_id}`` — статус.
* ``PATCH /hypotheses/compute-jobs/{job_id}/cancel`` — cancel-флаг.

CLAUDE.md §5 enforcement:
``PATCH .../review`` сохраняет ``reviewed_status='confirmed'`` и
``reviewed_by_user_id``, но **не мержит** доменные entities. Слияние —
отдельный flow Phase 4.6 с audit-log.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.enums import HypothesisType
from shared_models.orm import Hypothesis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parser_service.database import get_session
from parser_service.schemas import (
    BulkComputeRequest,
    HypothesisComputeJobResponse,
    HypothesisCreateRequest,
    HypothesisListResponse,
    HypothesisResponse,
    HypothesisReviewRequest,
    HypothesisSummary,
)
from parser_service.services.bulk_hypothesis_runner import (
    cancel_compute_job,
    enqueue_compute_job,
    execute_compute_job,
    get_compute_job,
)
from parser_service.services.hypothesis_runner import compute_hypothesis

router = APIRouter()


@router.post(
    "/trees/{tree_id}/hypotheses",
    response_model=HypothesisResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["hypotheses"],
    summary="Compute & persist a hypothesis between two subjects.",
    description=(
        "Запускает inference-engine rules для пары subjects и сохраняет "
        "результат как `Hypothesis` row + `HypothesisEvidence` rows. "
        "Idempotent: повторный вызов для той же пары + типа возвращает "
        "существующую гипотезу (без потери `reviewed_status`)."
    ),
)
async def create_hypothesis(
    tree_id: uuid.UUID,
    body: HypothesisCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisResponse:
    hyp = await compute_hypothesis(
        session,
        tree_id,
        body.subject_a_id,
        body.subject_b_id,
        HypothesisType(body.hypothesis_type),
    )
    if hyp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both subjects not found in this tree.",
        )
    await session.commit()
    # Lazy=raise + commit — нужно явно подтянуть evidences для сериализации.
    return await _load_full_hypothesis(session, hyp.id)


@router.get(
    "/trees/{tree_id}/hypotheses",
    response_model=HypothesisListResponse,
    tags=["hypotheses"],
    summary="List hypotheses in a tree (paginated, filterable).",
)
async def list_hypotheses(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    subject_id: uuid.UUID | None = Query(
        default=None,
        description=("Фильтр по subject_a_id ИЛИ subject_b_id — все гипотезы про одну сущность."),
    ),
    min_confidence: float = Query(default=0.5, ge=0.0, le=1.0),
    review_status: Literal["pending", "confirmed", "rejected", "deferred"] | None = Query(
        default=None,
        description=(
            "Фильтр по reviewed_status. ``deferred`` (Phase 4.9) — пользователь "
            "решил отложить решение; UI обычно прячет из дефолтного pending queue."
        ),
    ),
    hypothesis_type: Literal[
        "same_person",
        "parent_child",
        "siblings",
        "marriage",
        "duplicate_source",
        "duplicate_place",
    ]
    | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> HypothesisListResponse:
    """Пагинированный list, отсортированный по composite_score DESC."""
    query = select(Hypothesis).where(
        Hypothesis.tree_id == tree_id,
        Hypothesis.deleted_at.is_(None),
        Hypothesis.composite_score >= min_confidence,
    )
    count_query = select(func.count(Hypothesis.id)).where(
        Hypothesis.tree_id == tree_id,
        Hypothesis.deleted_at.is_(None),
        Hypothesis.composite_score >= min_confidence,
    )

    if subject_id is not None:
        condition = (Hypothesis.subject_a_id == subject_id) | (
            Hypothesis.subject_b_id == subject_id
        )
        query = query.where(condition)
        count_query = count_query.where(condition)

    if review_status is not None:
        query = query.where(Hypothesis.reviewed_status == review_status)
        count_query = count_query.where(Hypothesis.reviewed_status == review_status)

    if hypothesis_type is not None:
        query = query.where(Hypothesis.hypothesis_type == hypothesis_type)
        count_query = count_query.where(Hypothesis.hypothesis_type == hypothesis_type)

    total = int(await session.scalar(count_query) or 0)

    res = await session.execute(
        query.order_by(Hypothesis.composite_score.desc(), Hypothesis.id).limit(limit).offset(offset)
    )
    items = [HypothesisSummary.model_validate(h) for h in res.scalars().all()]
    return HypothesisListResponse(
        tree_id=tree_id,
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/hypotheses/{hypothesis_id}",
    response_model=HypothesisResponse,
    tags=["hypotheses"],
    summary="Get full hypothesis with evidences chain.",
)
async def get_hypothesis(
    hypothesis_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisResponse:
    return await _load_full_hypothesis(session, hypothesis_id)


@router.patch(
    "/hypotheses/{hypothesis_id}/review",
    response_model=HypothesisResponse,
    tags=["hypotheses"],
    summary="Mark hypothesis as confirmed/rejected (no auto-merge).",
    description=(
        "Сохраняет user judgment в `reviewed_status` + `reviewed_at` + "
        "`review_note`. **Не** мутирует доменные entities (CLAUDE.md §5). "
        "Слияние entities — отдельный flow Phase 4.6 с audit-log."
    ),
)
async def review_hypothesis(
    hypothesis_id: uuid.UUID,
    body: HypothesisReviewRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisResponse:
    hyp = (
        await session.execute(
            select(Hypothesis)
            .options(selectinload(Hypothesis.evidences))
            .where(
                Hypothesis.id == hypothesis_id,
                Hypothesis.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if hyp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hypothesis {hypothesis_id} not found",
        )

    hyp.reviewed_status = body.status
    hyp.review_note = body.note
    hyp.reviewed_at = dt.datetime.now(dt.UTC)
    # reviewed_by_user_id — Phase 7.3 заполнит из auth context.
    # Пока не трогаем (None допустим в migration).
    await session.commit()
    await session.refresh(hyp, attribute_names=["evidences"])
    return HypothesisResponse.model_validate(hyp)


# -----------------------------------------------------------------------------
# Phase 7.5 — bulk hypothesis compute.
# Sync-mode: POST блокирует HTTP-respond до завершения (или CANCELLED/FAILED).
# Idempotency на стороне сервиса: повторный POST в течение часа возвращает
# существующий job без переисполнения.
# -----------------------------------------------------------------------------


@router.post(
    "/trees/{tree_id}/hypotheses/compute-all",
    response_model=HypothesisComputeJobResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["hypotheses", "bulk-compute"],
    summary="Запустить bulk hypothesis-compute по всему дереву.",
    description=(
        "Создаёт `HypothesisComputeJob` и синхронно драйвит его до "
        "терминального статуса (succeeded / failed / cancelled). "
        "Idempotency 1 час: повторный POST возвращает существующий job "
        "(тот же id) без нового исполнения. `rule_ids` — optional whitelist; "
        "сейчас informational (см. PR #87 TODO для full filter)."
    ),
)
async def compute_all_hypotheses(
    tree_id: uuid.UUID,
    body: BulkComputeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisComputeJobResponse:
    job = await enqueue_compute_job(session, tree_id, rule_ids=body.rule_ids)
    # ``execute_compute_job`` сам идемпотентен по статусу: если job уже
    # RUNNING/SUCCEEDED/FAILED/CANCELLED — early-return. Поэтому безопасно
    # вызывать всегда (идемпотентный re-fetch возвращает существующий job).
    job = await execute_compute_job(session, job.id)
    return HypothesisComputeJobResponse.model_validate(job)


@router.get(
    "/trees/{tree_id}/hypotheses/compute-jobs/{job_id}",
    response_model=HypothesisComputeJobResponse,
    tags=["hypotheses", "bulk-compute"],
    summary="Статус bulk-compute job (для polling'а UI).",
)
async def get_compute_job_status(
    tree_id: uuid.UUID,
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisComputeJobResponse:
    job = await get_compute_job(session, job_id)
    if job is None or job.tree_id != tree_id:
        # Tree-mismatch trades information leak (existence vs cross-tree)
        # за чистый 404 для UI: «нет такого job'а в этом дереве». Полная
        # auth/RBAC — Phase 9.x, сейчас все деревья пользователя open.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Compute job {job_id} not found in tree {tree_id}",
        )
    return HypothesisComputeJobResponse.model_validate(job)


@router.patch(
    "/hypotheses/compute-jobs/{job_id}/cancel",
    response_model=HypothesisComputeJobResponse,
    tags=["hypotheses", "bulk-compute"],
    summary="Запросить cancel job'а (worker увидит между batch'ами).",
    description=(
        "Выставляет `cancel_requested = true`. Сам статус переходит в "
        "`cancelled` worker'ом при следующем batch-cycle. Для уже "
        "терминальных job'ов (succeeded/failed/cancelled) — no-op (200 + "
        "current state)."
    ),
)
async def request_cancel_compute_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisComputeJobResponse:
    job = await get_compute_job(session, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Compute job {job_id} not found",
        )
    job = await cancel_compute_job(session, job_id)
    return HypothesisComputeJobResponse.model_validate(job)


# -----------------------------------------------------------------------------


async def _load_full_hypothesis(
    session: AsyncSession,
    hypothesis_id: uuid.UUID,
) -> HypothesisResponse:
    """Подтянуть Hypothesis + evidences[] с eager-load."""
    hyp = (
        await session.execute(
            select(Hypothesis)
            .options(selectinload(Hypothesis.evidences))
            .where(
                Hypothesis.id == hypothesis_id,
                Hypothesis.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if hyp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hypothesis {hypothesis_id} not found",
        )
    return HypothesisResponse.model_validate(hyp)
