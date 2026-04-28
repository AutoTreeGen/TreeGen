"""Bulk hypothesis-compute service (Phase 7.5).

Расширение Phase 7.2: вместо вычисления одной пары — драйвит весь tree
через batched processing с прогрессом и cancel-флагом.

Lifecycle одного job'а:

1. ``enqueue_compute_job(session, tree_id, rule_ids=None)`` — idempotent
   create. Если уже есть active/recent job (≤ 1 час), возвращаем его.
   Иначе вставляем строку в ``hypothesis_compute_jobs`` со статусом
   ``QUEUED``.

2. ``execute_compute_job(session, job_id, batch_size=100)`` — реальная
   работа:

   * Перевод в ``RUNNING``, заполнение ``progress.total`` (count
     candidate pairs из ``dedup_finder``).
   * Loop по pairs, вызывая ``hypothesis_runner.compute_hypothesis``
     для каждой. Commit каждые ``batch_size`` итераций, обновляя
     ``progress.processed`` и ``hypotheses_created``.
   * Между batch'ами проверяем ``cancel_requested``: если true →
     status = ``CANCELLED``, finished_at, return.
   * На exception → status = ``FAILED`` + краткий ``error``, finished_at,
     re-raise.
   * На complete → status = ``SUCCEEDED``, finished_at.

3. ``cancel_compute_job(session, job_id)`` — выставляет
   ``cancel_requested = true``. Worker увидит между batch'ами.

Sync vs async
-------------
В dev / тесты: API endpoint вызывает ``enqueue_compute_job`` →
``execute_compute_job`` синхронно (await). Job переходит в SUCCEEDED
до того как HTTP response уйдёт.

Для prod (Phase 7.5+ или 3.5 background): подключим arq / cloud-tasks
producer. ``enqueue`` останется тем же (вставляет QUEUED row + публикует
job_id в очередь), а worker подцепит ``execute`` отдельным процессом.
Контракт сервиса не меняется — это compatible upgrade.

CLAUDE.md §5
------------
Bulk-runner использует ``hypothesis_runner.compute_hypothesis``, который
сам соблюдает READ-ONLY contract на доменные сущности. Bulk-runner
дополнительно мутирует только ``hypothesis_compute_jobs`` (свой статус /
прогресс). Никакого автоматического merge'а entities ни на каком слое.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from shared_models.enums import (
    HypothesisComputeJobStatus,
    HypothesisType,
)
from shared_models.orm import Hypothesis, HypothesisComputeJob
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.services.dedup_finder import (
    find_person_duplicates,
    find_place_duplicates,
    find_source_duplicates,
)
from parser_service.services.hypothesis_runner import compute_hypothesis
from parser_service.services.progress import ProgressPublisher

# Стадии bulk-compute job'а — публикуются в Redis pub/sub-канале
# ``job-events:{job_id}`` и форвардятся в браузер через SSE.
# Намеренно используем строковые литералы (а не общий ``Stage`` enum
# из ``progress.py``): import-pipeline и bulk-compute — разные домены,
# enum import'а не описывает «загрузка реестра правил» / «итерация
# по парам». Канал/формат события общие, набор стадий — нет.
STAGE_LOADING_RULES = "loading_rules"
STAGE_ITERATING_PAIRS = "iterating_pairs"
STAGE_PERSISTING = "persisting"
STAGE_SUCCEEDED = "succeeded"
STAGE_FAILED = "failed"
STAGE_CANCELLED = "cancelled"

# Idempotency window: повторный POST /compute-all внутри этого окна
# возвращает существующий job (если он QUEUED/RUNNING/SUCCEEDED).
# Час — pragmatic compromise: достаточно длинный, чтобы покрыть бoльшую
# часть user reflection time, но не блокирует «попробую ещё раз завтра».
_IDEMPOTENCY_WINDOW = dt.timedelta(hours=1)

# Active/recent статусы для idempotency: эти job'ы НЕ нужно
# «передоказывать» в течение window. FAILED / CANCELLED — наоборот,
# user'у обычно нужен retry, поэтому их excluding.
_IDEMPOTENT_STATUSES = (
    HypothesisComputeJobStatus.QUEUED.value,
    HypothesisComputeJobStatus.RUNNING.value,
    HypothesisComputeJobStatus.SUCCEEDED.value,
)

# Default batch size (см. brief: «100 persons per batch, commit между»).
# Каждые N pairs делаем commit + обновление progress + cancel-check.
_DEFAULT_BATCH_SIZE = 100

# Threshold для bulk: 0.0 = «все кандидаты, что прошли blocking». Это
# сознательно ниже Phase 7.2 default 0.5: bulk-режим хочет накопить
# даже weak hypotheses (для UI «низко-priority» категории), а не только
# strong ones.
_BULK_THRESHOLD = 0.0


@dataclass(slots=True)
class _CandidatePair:
    """Одна пара-кандидат для compute_hypothesis."""

    a_id: uuid.UUID
    b_id: uuid.UUID
    hypothesis_type: HypothesisType


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


async def enqueue_compute_job(
    session: AsyncSession,
    tree_id: uuid.UUID,
    rule_ids: list[str] | None = None,
    *,
    created_by_user_id: uuid.UUID | None = None,
) -> HypothesisComputeJob:
    """Idempotent create job для bulk-compute.

    Если в течение последнего часа уже есть QUEUED / RUNNING / SUCCEEDED
    job на этом дереве — возвращаем его (тот же id). Иначе вставляем
    новую строку.

    ``rule_ids`` — опциональный whitelist для фильтра правил Phase 7.5+
    (currently informational: сохраняется в job-row для audit, но worker
    использует default-rules pack из hypothesis_runner). Полная фильтрация
    планируется отдельным PR — это сохраняет API forward-compatible.
    """
    cutoff = dt.datetime.now(dt.UTC) - _IDEMPOTENCY_WINDOW
    existing = (
        await session.execute(
            select(HypothesisComputeJob)
            .where(
                HypothesisComputeJob.tree_id == tree_id,
                HypothesisComputeJob.status.in_(_IDEMPOTENT_STATUSES),
                # Recent job: либо started_at в окне (для running/succeeded),
                # либо QUEUED но created недавно (started_at NULL).
                (
                    (HypothesisComputeJob.started_at.is_(None))
                    | (HypothesisComputeJob.started_at >= cutoff)
                ),
            )
            .order_by(HypothesisComputeJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # mypy на pre-commit isolated env иногда не сужает scalar_one_or_none()
        # к типу select-таргета — явное приведение, чтобы не ломать checks.
        assert isinstance(existing, HypothesisComputeJob)
        return existing

    job = HypothesisComputeJob(
        tree_id=tree_id,
        created_by_user_id=created_by_user_id,
        status=HypothesisComputeJobStatus.QUEUED.value,
        rule_ids=rule_ids,
        progress={"processed": 0, "total": 0, "hypotheses_created": 0},
        cancel_requested=False,
    )
    session.add(job)
    await session.flush()
    return job


async def execute_compute_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    progress: ProgressPublisher | None = None,
) -> HypothesisComputeJob:
    """Drain'нуть один job: candidate pairs → compute_hypothesis loop.

    Idempotent на статусы: если job уже не QUEUED — возвращаем как есть
    (no-op). Это безопасно для retries и параллельных вызовов
    (двойной execute не повторит работу).

    Используется sync-режим: caller awaits, всё считается до return.
    Для prod-async (arq) сигнатура та же, но caller — отдельный worker.

    ``progress`` — опциональный publisher. Если задан, публикуем стадии
    ``loading_rules`` → ``iterating_pairs`` (между batch'ами) →
    ``persisting`` (последний batch) → ``succeeded`` / ``failed`` /
    ``cancelled``. Если ``None`` — bulk-runner работает молча (legacy
    sync-путь, БД-progress единственный источник истины).
    """
    publisher: ProgressPublisher = progress if progress is not None else ProgressPublisher(None, "")
    job = await _get_job_or_raise(session, job_id)

    if job.status != HypothesisComputeJobStatus.QUEUED.value:
        # Уже running / succeeded / failed / cancelled — никакого
        # ре-старта (idempotent).
        return job

    job.status = HypothesisComputeJobStatus.RUNNING.value
    job.started_at = dt.datetime.now(dt.UTC)
    await session.commit()

    try:
        # «Loading rule registry» — короткая стадия до того, как мы
        # начали enumerate'ить пары. Полезна в UI как первый сигнал
        # «job стартовал», даже если total=0 candidate pair'ов.
        await publisher.publish(
            STAGE_LOADING_RULES,
            current=0,
            total=1,
            message="Loading rule registry",
        )
        pairs = await _enumerate_candidate_pairs(session, job.tree_id)

        # Pre-fill total. hypotheses_created copy-pre-existing? Нет —
        # ``compute_hypothesis`` идемпотентен; считаем "created" любые
        # вызовы которые вернули hypothesis row (включая existing ones).
        # Это user-visible "сколько hypothesis в результате есть для
        # этого job'а", не "сколько свежих INSERT'ов".
        job.progress = {
            "processed": 0,
            "total": len(pairs),
            "hypotheses_created": 0,
        }
        await session.commit()
        await publisher.publish(
            STAGE_ITERATING_PAIRS,
            current=0,
            total=len(pairs),
            message=f"Iterating person pairs (0/{len(pairs)})",
        )

        processed = 0
        created = 0

        for pair in pairs:
            result = await compute_hypothesis(
                session,
                job.tree_id,
                pair.a_id,
                pair.b_id,
                pair.hypothesis_type,
            )
            processed += 1
            if result is not None:
                created += 1

            if processed % batch_size == 0 or processed == len(pairs):
                job.progress = {
                    "processed": processed,
                    "total": len(pairs),
                    "hypotheses_created": created,
                }
                await session.commit()
                # Persisting — короткая нота для UI: «commit прошёл,
                # вот свежие counters». Финальный batch отдельно
                # маркируем (см. ниже после loop'а).
                if processed < len(pairs):
                    await publisher.publish(
                        STAGE_ITERATING_PAIRS,
                        current=processed,
                        total=len(pairs),
                        message=f"Iterating person pairs ({processed}/{len(pairs)})",
                    )

                # Cancel-check: re-read row freshly (после commit'а
                # SQLAlchemy expire'ает все объекты — refresh подтянет
                # cancel_requested из БД, не из session-кэша).
                await session.refresh(job, ["cancel_requested"])
                if job.cancel_requested:
                    job.status = HypothesisComputeJobStatus.CANCELLED.value
                    job.finished_at = dt.datetime.now(dt.UTC)
                    await session.commit()
                    await publisher.publish(
                        STAGE_CANCELLED,
                        current=processed,
                        total=len(pairs),
                        message="Cancelled by user",
                    )
                    return job

        await publisher.publish(
            STAGE_PERSISTING,
            current=processed,
            total=len(pairs),
            message="Persisting hypotheses",
        )
        job.status = HypothesisComputeJobStatus.SUCCEEDED.value
        job.finished_at = dt.datetime.now(dt.UTC)
        # Финальный progress (на случай если последний batch не выровнялся).
        job.progress = {
            "processed": processed,
            "total": len(pairs),
            "hypotheses_created": created,
        }
        await session.commit()
        await publisher.publish(
            STAGE_SUCCEEDED,
            current=processed,
            total=len(pairs),
            message=f"Created {created} hypotheses",
        )
        return job

    except Exception as exc:
        # Откатываем текущую (возможно частично записанную) транзакцию,
        # чтобы можно было отдельно записать FAILED-статус.
        await session.rollback()
        job = await _get_job_or_raise(session, job_id)
        job.status = HypothesisComputeJobStatus.FAILED.value
        job.error = (str(exc) or type(exc).__name__)[:2000]
        job.finished_at = dt.datetime.now(dt.UTC)
        await session.commit()
        await publisher.publish(
            STAGE_FAILED,
            current=0,
            total=1,
            message=f"failed: {exc}",
        )
        raise


async def cancel_compute_job(
    session: AsyncSession,
    job_id: uuid.UUID,
) -> HypothesisComputeJob:
    """Выставить ``cancel_requested = True``.

    Сам статус worker'ом меняется на CANCELLED при следующем batch-cycle.
    Если job уже не RUNNING/QUEUED — no-op (возвращаем как есть).
    """
    job = await _get_job_or_raise(session, job_id)
    if job.status in (
        HypothesisComputeJobStatus.QUEUED.value,
        HypothesisComputeJobStatus.RUNNING.value,
    ):
        job.cancel_requested = True
        await session.commit()
    return job


async def get_compute_job(session: AsyncSession, job_id: uuid.UUID) -> HypothesisComputeJob | None:
    """Read-only fetch job по id. ``None`` если нет."""
    result: HypothesisComputeJob | None = (
        await session.execute(select(HypothesisComputeJob).where(HypothesisComputeJob.id == job_id))
    ).scalar_one_or_none()
    return result


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


async def _get_job_or_raise(session: AsyncSession, job_id: uuid.UUID) -> HypothesisComputeJob:
    job = await get_compute_job(session, job_id)
    if job is None:
        msg = f"Compute job {job_id} not found"
        raise LookupError(msg)
    return job


async def _enumerate_candidate_pairs(
    session: AsyncSession, tree_id: uuid.UUID
) -> list[_CandidatePair]:
    """Собрать candidate pairs из dedup_finder для всего дерева.

    Используем threshold=0.0: bulk-mode хочет даже weak пары — UI
    отфильтрует. Это ниже Phase 7.2 default 0.5; по дизайну.

    Pairs из всех трёх категорий собираем в один list. compute_hypothesis
    в hypothesis_runner сам делает canonical-order по (a, b), так что
    направление здесь не важно.
    """
    pairs: list[_CandidatePair] = []

    person_pairs = await find_person_duplicates(session, tree_id, threshold=_BULK_THRESHOLD)
    pairs.extend(
        _CandidatePair(
            a_id=s.entity_a_id,
            b_id=s.entity_b_id,
            hypothesis_type=HypothesisType.SAME_PERSON,
        )
        for s in person_pairs
    )

    source_pairs = await find_source_duplicates(session, tree_id, threshold=_BULK_THRESHOLD)
    pairs.extend(
        _CandidatePair(
            a_id=s.entity_a_id,
            b_id=s.entity_b_id,
            hypothesis_type=HypothesisType.DUPLICATE_SOURCE,
        )
        for s in source_pairs
    )

    place_pairs = await find_place_duplicates(session, tree_id, threshold=_BULK_THRESHOLD)
    pairs.extend(
        _CandidatePair(
            a_id=s.entity_a_id,
            b_id=s.entity_b_id,
            hypothesis_type=HypothesisType.DUPLICATE_PLACE,
        )
        for s in place_pairs
    )

    return pairs


async def _count_hypotheses_for_tree(session: AsyncSession, tree_id: uuid.UUID) -> int:
    """Diagnostic helper для тестов / debug. Возвращает кол-во hypotheses.

    Вынесено отдельной функцией чтобы не тащить SQL-импорты в тестовый
    модуль и держать единый стиль обращения к ``Hypothesis``.
    """
    total = await session.scalar(
        select(func.count(Hypothesis.id)).where(
            Hypothesis.tree_id == tree_id,
            Hypothesis.deleted_at.is_(None),
        )
    )
    return int(total or 0)


__all__ = [
    "cancel_compute_job",
    "enqueue_compute_job",
    "execute_compute_job",
    "get_compute_job",
]
