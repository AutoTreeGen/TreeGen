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
import os
import uuid
from typing import Annotated, Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from shared_models import TreeRole
from shared_models.enums import HypothesisType
from shared_models.orm import Hypothesis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parser_service.auth import RequireUser
from parser_service.database import get_session
from parser_service.queue import get_arq_pool
from parser_service.schemas import (
    BulkComputeRequest,
    HypothesisComputeJobResponse,
    HypothesisCreateRequest,
    HypothesisListResponse,
    HypothesisRecomputeScoresResponse,
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
from parser_service.services.hypothesis_score_recompute import (
    RECOMPUTE_ALGORITHM_VERSION,
    recompute_all_hypothesis_scores,
)
from parser_service.services.metrics import hypothesis_review_action_total
from parser_service.services.permissions import require_tree_role

router = APIRouter()

# Имя arq-функции, которую вызывает worker для bulk hypothesis compute.
# Захардкожено как строка чтобы не плодить cross-import между HTTP-слоем
# и worker-модулем (см. зеркальную RUN_IMPORT_JOB_NAME в imports.py).
RUN_BULK_HYPOTHESIS_JOB_NAME = "run_bulk_hypothesis_job"

# Шаблон относительного URL SSE-эндпоинта для bulk-compute job'а.
# Полный путь монтируется в main.py: /trees/{tree_id}/hypotheses/compute-jobs/{job_id}/events.
_EVENTS_URL_TEMPLATE = "/trees/{tree_id}/hypotheses/compute-jobs/{job_id}/events"

# Env-флаг для inline-режима (зеркало imports._INLINE_ENV_VAR): когда
# выставлен в "1", POST /compute-all исполняется синхронно (как до
# Phase 7.5 finalize). Используется conftest.py чтобы старые тесты
# (test_bulk_hypothesis_compute.py) видели готовый succeeded-job в
# response, а не 202 + worker-driven flow. Async-путь — дефолт.
_INLINE_ENV_VAR = "PARSER_SERVICE_BULK_COMPUTE_INLINE"


def _events_url(tree_id: uuid.UUID, job_id: uuid.UUID) -> str:
    """Сформировать относительный URL SSE-эндпоинта для bulk-compute job."""
    return _EVENTS_URL_TEMPLATE.format(tree_id=tree_id, job_id=job_id)


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
    # Phase 9.0: review action counter (после commit — считаем только
    # успешно сохранённые judgements).
    hypothesis_review_action_total.labels(action=body.status).inc()
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
    status_code=status.HTTP_202_ACCEPTED,
    tags=["hypotheses", "bulk-compute"],
    summary="Запустить bulk hypothesis-compute по всему дереву.",
    description=(
        "Создаёт `HypothesisComputeJob`, ставит его в arq-очередь "
        "``imports`` под job-функцией ``run_bulk_hypothesis_job`` и "
        "возвращает 202 Accepted с ``events_url`` (SSE-стрим прогресса). "
        "Idempotency 1 час: повторный POST возвращает существующий job "
        "(тот же id) без нового enqueue. `rule_ids` — optional whitelist; "
        "сейчас informational (см. PR #87 TODO для full filter).\n\n"
        "**Inline-режим:** если ``PARSER_SERVICE_BULK_COMPUTE_INLINE=1``, "
        "хендлер исполняет ``execute_compute_job`` синхронно и возвращает "
        "201 Created с уже-терминальным job'ом. Используется в тестах и "
        "CLI-сценариях без воркера."
    ),
)
async def compute_all_hypotheses(
    tree_id: uuid.UUID,
    body: BulkComputeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    response: Response,
) -> HypothesisComputeJobResponse:
    job = await enqueue_compute_job(session, tree_id, rule_ids=body.rule_ids)
    await session.commit()

    if os.environ.get(_INLINE_ENV_VAR) == "1":
        # Legacy-синхронный путь: исполнить job-loop в текущей коротине.
        # ``execute_compute_job`` сам идемпотентен по статусу — повторный
        # вызов на не-QUEUED job отдаёт его как есть. Возвращаем 201 +
        # events_url=None (SSE для inline-job'а смысла не имеет).
        job = await execute_compute_job(session, job.id)
        response.status_code = status.HTTP_201_CREATED
        return HypothesisComputeJobResponse.model_validate(job)

    # Async-путь (дефолт): ставим job в очередь, отдаём 202 Accepted.
    # Передаём только UUID-строку — worker сам подгружает row и драйвит.
    # _job_id здесь = arq-job id (для дедупа постановок); HypothesisComputeJob.id
    # — наш бизнес-id, его и используем как arq job_id (один-к-одному).
    await pool.enqueue_job(
        RUN_BULK_HYPOTHESIS_JOB_NAME,
        str(job.id),
        _job_id=f"bulk-hypothesis:{job.id}",
    )
    payload = HypothesisComputeJobResponse.model_validate(job)
    return payload.model_copy(update={"events_url": _events_url(tree_id, job.id)})


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
    payload = HypothesisComputeJobResponse.model_validate(job)
    # events_url возвращаем чтобы UI не сбрасывал SSE — он подождёт
    # терминального события от worker'а (CANCELLED). Для уже-terminal
    # job'ов worker не публикует ничего нового, но и SSE не подключён.
    return payload.model_copy(update={"events_url": _events_url(job.tree_id, job.id)})


# -----------------------------------------------------------------------------
# Phase 7.5 — recompute composite_score with aggregation v2 (ADR-0065).
# -----------------------------------------------------------------------------


@router.post(
    "/trees/{tree_id}/hypotheses/recompute-scores",
    response_model=HypothesisRecomputeScoresResponse,
    status_code=status.HTTP_200_OK,
    tags=["hypotheses"],
    summary="Пересчитать composite_score у всех гипотез дерева через v2-aggregation.",
    description=(
        "Phase 7.5 (ADR-0065). Используется когда algorithm aggregation "
        "сменился, а persisted hypotheses держат старые scores. Не запускает "
        "rules заново — пересчитывает только из persisted ``HypothesisEvidence``-"
        "rows через ``inference_engine.aggregate_confidence``. Идемпотентно. "
        "Не трогает ``reviewed_status`` (user judgment сохраняется). "
        "Owner-only: triggers AuditLog row."
    ),
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def recompute_hypothesis_scores(
    tree_id: uuid.UUID,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HypothesisRecomputeScoresResponse:
    result = await recompute_all_hypothesis_scores(
        session,
        tree_id,
        actor_user_id=user_id,
    )
    await session.commit()
    return HypothesisRecomputeScoresResponse(
        tree_id=result.tree_id,
        algorithm=RECOMPUTE_ALGORITHM_VERSION,
        recomputed_count=result.recomputed_count,
        mean_absolute_delta=result.mean_absolute_delta,
        max_absolute_delta=result.max_absolute_delta,
    )


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
