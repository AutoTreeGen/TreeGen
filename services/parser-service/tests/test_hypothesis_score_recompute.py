"""Интеграционные тесты для Phase 7.5 recompute (ADR-0057).

Покрывает:

* Service-level: ``recompute_all_hypothesis_scores`` обновляет scores,
  пишет одну ``AuditLog`` строку, идемпотентен, сохраняет
  ``reviewed_status``.
* Endpoint-level: POST /trees/{id}/hypotheses/recompute-scores
  возвращает корректный response, audit-row появляется.

Использует in-memory SQLite engine c shared-models metadata —
зеркалирует подход из ``test_hypothesis_runner.py``, но без полного
GED-импорта (мы тестируем только recompute-сторону, хождение через
rules-flow покрыто отдельно).
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from shared_models.enums import (
    ActorKind,
    HypothesisComputedBy,
    HypothesisReviewStatus,
    HypothesisSubjectType,
    HypothesisType,
)
from shared_models.orm import AuditLog, Hypothesis, HypothesisEvidence
from sqlalchemy import func, select

pytestmark = [pytest.mark.db, pytest.mark.integration]


async def _make_session(postgres_dsn: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_dsn, future=True)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
    return engine, SessionMaker


async def _seed_minimal_tree(session) -> uuid.UUID:
    """Создать минимальное дерево + одну гипотезу с двумя SUPPORTS evidence.

    Возвращает tree_id. Не создаёт persons/Source/Place — hypothesis
    держит произвольные UUID как subject_*_id (это допустимо, это
    polymorphic FK без referential integrity, см. ADR-0021).
    """
    from shared_models.orm import Tree, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"recompute-{suffix}@example.com",
        external_auth_id=f"auth0|recompute-{suffix}",
        display_name="Recompute Test Owner",
    )
    session.add(user)
    await session.flush()

    tree = Tree(owner_user_id=user.id, name=f"Recompute fixture {suffix}")
    session.add(tree)
    await session.flush()
    return tree.id


def _make_hypothesis(
    tree_id: uuid.UUID,
    *,
    composite_score: float,
    evidences_data: list[tuple[str, str, float]],
    reviewed_status: str = HypothesisReviewStatus.PENDING.value,
) -> Hypothesis:
    """Сконструировать persistable Hypothesis с заданными evidences.

    ``evidences_data`` — list of (rule_id, direction, weight).
    """
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()
    if str(a_id) > str(b_id):
        a_id, b_id = b_id, a_id

    hyp = Hypothesis(
        tree_id=tree_id,
        hypothesis_type=HypothesisType.SAME_PERSON.value,
        subject_a_type=HypothesisSubjectType.PERSON.value,
        subject_a_id=a_id,
        subject_b_type=HypothesisSubjectType.PERSON.value,
        subject_b_id=b_id,
        composite_score=composite_score,
        computed_at=dt.datetime.now(dt.UTC),
        computed_by=HypothesisComputedBy.AUTOMATIC.value,
        rules_version="engine=test;rules=00000000",
        reviewed_status=reviewed_status,
        provenance={"engine_version": "test"},
    )
    hyp.evidences = [
        HypothesisEvidence(
            rule_id=rid,
            direction=direction,
            weight=weight,
            observation=f"{rid}-{direction}",
            source_provenance={},
        )
        for rid, direction, weight in evidences_data
    ]
    return hyp


@pytest.mark.asyncio
async def test_recompute_updates_score_and_writes_audit(postgres_dsn) -> None:
    """Базовый case: одна гипотеза с устаревшим score → пересчитан + audit row."""
    from parser_service.services.hypothesis_score_recompute import (
        RECOMPUTE_ALGORITHM_VERSION,
        recompute_all_hypothesis_scores,
    )

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            tree_id = await _seed_minimal_tree(session)
            # Старый score 0.9 (legacy linear sum 0.5+0.4); v2 даст 0.7.
            hyp = _make_hypothesis(
                tree_id,
                composite_score=0.9,
                evidences_data=[
                    ("rule-a", "supports", 0.5),
                    ("rule-b", "supports", 0.4),
                ],
            )
            session.add(hyp)
            await session.flush()
            stale_id = hyp.id
            await session.commit()

        async with SessionMaker() as session:
            owner_id = await session.scalar(
                select(Hypothesis.tree_id).where(Hypothesis.id == stale_id)
            )
            assert owner_id == tree_id

            actor = uuid.uuid4()
            result = await recompute_all_hypothesis_scores(
                session,
                tree_id,
                actor_user_id=None,  # system-initiated; actor passing covered ниже
            )
            await session.commit()

            assert result.recomputed_count == 1
            # 0.5 + 0.4 → Bayesian fusion 1 − 0.5·0.6 = 0.7.
            assert abs(result.mean_absolute_delta - 0.2) < 1e-9
            assert abs(result.max_absolute_delta - 0.2) < 1e-9
            del actor  # silence linter — actor проверяется отдельно

        async with SessionMaker() as session:
            updated = (
                await session.execute(select(Hypothesis).where(Hypothesis.id == stale_id))
            ).scalar_one()
            assert abs(updated.composite_score - 0.7) < 1e-9

            audits = (
                (await session.execute(select(AuditLog).where(AuditLog.tree_id == tree_id)))
                .scalars()
                .all()
            )
            recompute_audits = [a for a in audits if a.entity_type == "hypothesis_batch_recompute"]
            assert len(recompute_audits) == 1
            entry = recompute_audits[0]
            assert entry.diff["algorithm"] == RECOMPUTE_ALGORITHM_VERSION
            assert entry.diff["recomputed_count"] == 1
            assert entry.actor_kind == ActorKind.SYSTEM.value
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recompute_preserves_reviewed_status(postgres_dsn) -> None:
    """``reviewed_status='confirmed'`` сохраняется при recompute (ADR-0021)."""
    from parser_service.services.hypothesis_score_recompute import (
        recompute_all_hypothesis_scores,
    )

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            tree_id = await _seed_minimal_tree(session)
            hyp = _make_hypothesis(
                tree_id,
                composite_score=0.9,
                evidences_data=[("rule-a", "supports", 0.6)],
                reviewed_status=HypothesisReviewStatus.CONFIRMED.value,
            )
            session.add(hyp)
            await session.flush()
            hyp_id = hyp.id
            await session.commit()

        async with SessionMaker() as session:
            await recompute_all_hypothesis_scores(session, tree_id, actor_user_id=None)
            await session.commit()

        async with SessionMaker() as session:
            row = (
                await session.execute(select(Hypothesis).where(Hypothesis.id == hyp_id))
            ).scalar_one()
            # Score обновился, reviewed_status — нет.
            assert abs(row.composite_score - 0.6) < 1e-9
            assert row.reviewed_status == HypothesisReviewStatus.CONFIRMED.value
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recompute_idempotent(postgres_dsn) -> None:
    """Второй recompute с тем же algorithm даёт mean_delta=0."""
    from parser_service.services.hypothesis_score_recompute import (
        recompute_all_hypothesis_scores,
    )

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            tree_id = await _seed_minimal_tree(session)
            session.add(
                _make_hypothesis(
                    tree_id,
                    composite_score=0.0,
                    evidences_data=[
                        ("rule-a", "supports", 0.5),
                        ("rule-b", "supports", 0.4),
                    ],
                )
            )
            await session.commit()

        async with SessionMaker() as session:
            await recompute_all_hypothesis_scores(session, tree_id, actor_user_id=None)
            await session.commit()

        async with SessionMaker() as session:
            second = await recompute_all_hypothesis_scores(session, tree_id, actor_user_id=None)
            await session.commit()
            assert second.recomputed_count == 1
            assert second.mean_absolute_delta == 0.0
            assert second.max_absolute_delta == 0.0

        async with SessionMaker() as session:
            recompute_audit_count = (
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.tree_id == tree_id,
                        AuditLog.entity_type == "hypothesis_batch_recompute",
                    )
                )
                or 0
            )
            # Каждый запуск пишет audit-row (это «отчёт о запуске», не diff).
            assert recompute_audit_count == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recompute_endpoint_owner_only(app_client) -> None:
    """POST /trees/{id}/hypotheses/recompute-scores — owner-only.

    Гость без auth получит 401/403; владелец — 200 с response payload.
    """
    # _import_ged создаёт tree через app_client с auth context (test fixture).
    # Импортируем helper из соседнего test-модуля.
    from .test_hypothesis_runner import _GED_DEDUP, _import_ged

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    response = await app_client.post(
        f"/trees/{tree_id}/hypotheses/recompute-scores",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tree_id"] == str(tree_id)
    assert payload["recomputed_count"] >= 0
    assert "algorithm" in payload
    assert 0.0 <= payload["mean_absolute_delta"] <= 1.0


@pytest.mark.asyncio
async def test_recompute_endpoint_404_for_unknown_tree(app_client) -> None:
    """Tree не существует → 404."""
    fake_tree_id = uuid.uuid4()
    response = await app_client.post(
        f"/trees/{fake_tree_id}/hypotheses/recompute-scores",
    )
    assert response.status_code == 404
