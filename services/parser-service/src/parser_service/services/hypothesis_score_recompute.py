"""Phase 7.5 — recompute composite_score для всех гипотез дерева.

Когда aggregation algorithm меняется (Phase 7.5: с linear weighted-sum
на Bayesian fusion), persisted hypotheses держат старые scores. Чтобы
не дёргать ``hypothesis_runner.compute_hypothesis`` (который заново
гонит rules через домен и зависит от свежих данных), мы пересчитываем
composite только из persisted ``HypothesisEvidence``-rows через
``inference_engine.aggregate_confidence``.

Идемпотентно: повторный recompute даёт те же значения. Не трогает
``rules_version`` (это маркер «какие rules были применены», а algorithm
aggregation — отдельная ось). Не мутирует ``reviewed_status``: user
judgment сохраняется (ADR-0021 §«Idempotency»).

Audit: одна запись в ``audit_log`` на batch (а не на hypothesis), чтобы
не раздувать таблицу. ``entity_type='hypothesis_batch_recompute'``,
``entity_id=tree_id``, ``action=update``, diff содержит {algorithm,
recomputed_count, mean_delta}. Это даёт минимальный паттерн для
forensics: «на этом дереве в день N запускался recompute с algorithm
X, изменено столько-то строк».

CLAUDE.md §5: recompute READ-only на доменные сущности (persons и т.п.);
мутирует только ``hypotheses.composite_score`` + одну audit-row.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from inference_engine import Evidence as EngineEvidence
from inference_engine import EvidenceDirection, aggregate_confidence
from shared_models.enums import ActorKind, AuditAction
from shared_models.orm import AuditLog, Hypothesis
from shared_models.types import new_uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Маркер algorithm-версии. Появляется в audit-diff'е, чтобы при отладке
# можно было понять, каким algorithm'ом считались scores. Любое будущее
# изменение в ``aggregate_confidence`` должно бампить эту константу
# (по semver — minor для совместимых tweaks, major для смены формулы).
RECOMPUTE_ALGORITHM_VERSION = "v2-bayesian-fusion-2026-04"


@dataclass(slots=True, frozen=True)
class RecomputeResult:
    """Сводка по одному запуску recompute.

    Attributes:
        tree_id: Дерево, в котором пересчитали.
        recomputed_count: Сколько hypothesis-rows получили новый score.
            Включая no-op'ы (если score не поменялся — всё равно считаем
            «обработанной»; реальные diffs смотри через mean_delta).
        mean_absolute_delta: Среднее |old − new| по всем rows. Полезный
            sanity-check: если ≈ 0, recompute ничего не двигал и можно
            подозревать что algorithm не сменился.
        max_absolute_delta: Самое большое изменение score'а; для outlier-
            детекции в audit-логе.
    """

    tree_id: uuid.UUID
    recomputed_count: int
    mean_absolute_delta: float
    max_absolute_delta: float


async def recompute_all_hypothesis_scores(
    session: AsyncSession,
    tree_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None,
) -> RecomputeResult:
    """Пересчитать composite_score для всех hypothesis-rows дерева.

    Args:
        session: Async SQLAlchemy session. Caller отвечает за commit.
        tree_id: ID дерева. Soft-deleted hypotheses пропускаются.
        actor_user_id: Кто инициировал (для audit_log.actor_user_id).
            ``None`` — system-initiated (например, миграция).

    Returns:
        :class:`RecomputeResult` со статистикой запуска.

    Side effects:
        * Обновляет ``hypotheses.composite_score`` для всех not-deleted
          rows этого дерева.
        * Добавляет одну ``AuditLog`` row (action=UPDATE, entity_type=
          ``hypothesis_batch_recompute``, entity_id=tree_id).
        * НЕ commit'ит — caller контролирует транзакцию.
    """
    rows = (
        (
            await session.execute(
                select(Hypothesis)
                .options(selectinload(Hypothesis.evidences))
                .where(
                    Hypothesis.tree_id == tree_id,
                    Hypothesis.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    deltas: list[float] = []
    for hyp in rows:
        engine_evs = [_orm_to_engine_evidence(ev) for ev in hyp.evidences]
        new_score = aggregate_confidence(engine_evs).composite_score
        deltas.append(abs(hyp.composite_score - new_score))
        hyp.composite_score = new_score

    if deltas:
        mean_delta = sum(deltas) / len(deltas)
        max_delta = max(deltas)
    else:
        mean_delta = 0.0
        max_delta = 0.0

    # Audit-row пишем всегда (даже если nothing changed) — это «отчёт о
    # запуске», а не «отчёт об изменениях». Forensics: «когда последний
    # раз гоняли recompute v2 на этом дереве?».
    session.add(
        AuditLog(
            id=new_uuid(),
            tree_id=tree_id,
            entity_type="hypothesis_batch_recompute",
            entity_id=tree_id,
            action=AuditAction.UPDATE.value,
            actor_user_id=actor_user_id,
            actor_kind=ActorKind.USER.value if actor_user_id else ActorKind.SYSTEM.value,
            import_job_id=None,
            reason="Phase 7.5 confidence aggregation v2 (ADR-0065)",
            diff={
                "algorithm": RECOMPUTE_ALGORITHM_VERSION,
                "recomputed_count": len(rows),
                "mean_absolute_delta": mean_delta,
                "max_absolute_delta": max_delta,
            },
            created_at=dt.datetime.now(dt.UTC),
        )
    )

    return RecomputeResult(
        tree_id=tree_id,
        recomputed_count=len(rows),
        mean_absolute_delta=mean_delta,
        max_absolute_delta=max_delta,
    )


def _orm_to_engine_evidence(ev: object) -> EngineEvidence:
    """Конвертировать ORM ``HypothesisEvidence`` в in-memory ``Evidence``.

    Используется только для recompute-pipeline: aggregate_confidence
    работает с in-memory-моделями, ORM-rows надо отобразить 1:1.
    """
    return EngineEvidence(
        rule_id=ev.rule_id,  # type: ignore[attr-defined]
        direction=EvidenceDirection(ev.direction),  # type: ignore[attr-defined]
        weight=ev.weight,  # type: ignore[attr-defined]
        observation=ev.observation,  # type: ignore[attr-defined]
        source_provenance=dict(ev.source_provenance),  # type: ignore[attr-defined]
    )


__all__ = [
    "RECOMPUTE_ALGORITHM_VERSION",
    "RecomputeResult",
    "recompute_all_hypothesis_scores",
]
