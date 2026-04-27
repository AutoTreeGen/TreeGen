"""Интеграционные тесты hypothesis_runner (Phase 7.2 Task 3).

Использует существующий ``app_client`` + ``postgres_dsn`` из conftest:
импорт GED через API → вызов hypothesis_runner напрямую через session.

CLAUDE.md §5: hypothesis_runner READ-ONLY на доменные сущности.
test_no_domain_entity_mutations проверяет, что count'ы persons /
sources / places не меняются.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


# Тот же fixture-минимум, что в Phase 3.4 dedup tests: пара
# Zhitnitzky / Zhytnicki + два source с похожим title.
_GED_DEDUP = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Meir /Zhitnitzky/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
0 @I2@ INDI
1 NAME Meir /Zhytnicki/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 @S1@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
0 TRLR
"""


async def _import_ged(app_client, ged_bytes: bytes) -> uuid.UUID:
    files = {"file": ("test.ged", ged_bytes, "application/octet-stream")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["tree_id"])


async def _make_session(postgres_dsn: str):  # pragma: no cover — helper
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_dsn, future=True)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
    return engine, SessionMaker


@pytest.mark.asyncio
async def test_compute_zhitnitzky_hypothesis_persists(app_client, postgres_dsn) -> None:
    """compute_hypothesis для Zhitnitzky/Zhytnicki создаёт row + evidences.

    Это главный demo Phase 7.2: связка Phase 7.1 rules → ORM rows.
    """
    from parser_service.services.hypothesis_runner import compute_hypothesis
    from shared_models.enums import HypothesisType
    from shared_models.orm import Hypothesis, HypothesisEvidence, Person
    from sqlalchemy import select

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            # Получить ids двух Zhitnitzky персон.
            persons = (
                (await session.execute(select(Person).where(Person.tree_id == tree_id)))
                .scalars()
                .all()
            )
            i1 = next(p for p in persons if p.gedcom_xref == "I1")
            i2 = next(p for p in persons if p.gedcom_xref == "I2")

            hyp = await compute_hypothesis(
                session, tree_id, i1.id, i2.id, HypothesisType.SAME_PERSON
            )
            assert hyp is not None
            assert hyp.hypothesis_type == "same_person"
            assert hyp.composite_score >= 0.85, (
                f"expected ≥0.85 для Zhitnitzky pair, got {hyp.composite_score}"
            )
            assert hyp.reviewed_status == "pending"
            assert hyp.subject_a_type == "person"

            # Persist + commit, потом проверим через свежую сессию.
            await session.commit()

        async with SessionMaker() as session:
            stored = (
                (await session.execute(select(Hypothesis).where(Hypothesis.tree_id == tree_id)))
                .scalars()
                .all()
            )
            assert len(stored) == 1
            stored_hyp = stored[0]

            evidences = (
                (
                    await session.execute(
                        select(HypothesisEvidence).where(
                            HypothesisEvidence.hypothesis_id == stored_hyp.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            # Phase 7.1 rule pack: surname + birth_year + birth_place
            # = три SUPPORTS evidence'а (sex_consistency silent при M+M).
            rule_ids = {ev.rule_id for ev in evidences}
            assert "surname_dm_match" in rule_ids
            assert "birth_year_match" in rule_ids
            assert "birth_place_match" in rule_ids
            for ev in evidences:
                assert 0.0 <= ev.weight <= 1.0
                assert ev.observation
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_compute_canonical_id_order_idempotent(app_client, postgres_dsn) -> None:
    """compute(a, b) и compute(b, a) → ровно одна row благодаря canonical order."""
    from parser_service.services.hypothesis_runner import compute_hypothesis
    from shared_models.enums import HypothesisType
    from shared_models.orm import Hypothesis, Person
    from sqlalchemy import func, select

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            persons = (
                (await session.execute(select(Person).where(Person.tree_id == tree_id)))
                .scalars()
                .all()
            )
            i1 = next(p for p in persons if p.gedcom_xref == "I1")
            i2 = next(p for p in persons if p.gedcom_xref == "I2")

            await compute_hypothesis(session, tree_id, i1.id, i2.id, HypothesisType.SAME_PERSON)
            await session.commit()

        async with SessionMaker() as session:
            # Re-run в обратном порядке.
            persons = (
                (await session.execute(select(Person).where(Person.tree_id == tree_id)))
                .scalars()
                .all()
            )
            i1 = next(p for p in persons if p.gedcom_xref == "I1")
            i2 = next(p for p in persons if p.gedcom_xref == "I2")
            await compute_hypothesis(session, tree_id, i2.id, i1.id, HypothesisType.SAME_PERSON)
            await session.commit()

        async with SessionMaker() as session:
            count = await session.scalar(
                select(func.count(Hypothesis.id)).where(Hypothesis.tree_id == tree_id)
            )
            assert count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rerun_preserves_reviewed_status(app_client, postgres_dsn) -> None:
    """Re-run после user review не сбрасывает reviewed_status.

    User confirm → re-run hypothesis_runner (например, при апгрейде rules) →
    reviewed_status='confirmed' сохраняется.
    """
    from parser_service.services.hypothesis_runner import compute_hypothesis
    from shared_models.enums import HypothesisReviewStatus, HypothesisType
    from shared_models.orm import Hypothesis, Person
    from sqlalchemy import select, update

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            persons = (
                (await session.execute(select(Person).where(Person.tree_id == tree_id)))
                .scalars()
                .all()
            )
            i1 = next(p for p in persons if p.gedcom_xref == "I1")
            i2 = next(p for p in persons if p.gedcom_xref == "I2")
            hyp = await compute_hypothesis(
                session, tree_id, i1.id, i2.id, HypothesisType.SAME_PERSON
            )
            hyp_id = hyp.id
            await session.commit()

        # User помечает confirmed.
        async with SessionMaker() as session:
            await session.execute(
                update(Hypothesis)
                .where(Hypothesis.id == hyp_id)
                .values(reviewed_status=HypothesisReviewStatus.CONFIRMED.value)
            )
            await session.commit()

        # Симулируем re-run при апгрейде rules: переопределяем rules_version.
        async with SessionMaker() as session:
            await session.execute(
                update(Hypothesis)
                .where(Hypothesis.id == hyp_id)
                .values(rules_version="OLD_VERSION")
            )
            await session.commit()

        async with SessionMaker() as session:
            persons = (
                (await session.execute(select(Person).where(Person.tree_id == tree_id)))
                .scalars()
                .all()
            )
            i1 = next(p for p in persons if p.gedcom_xref == "I1")
            i2 = next(p for p in persons if p.gedcom_xref == "I2")
            updated = await compute_hypothesis(
                session, tree_id, i1.id, i2.id, HypothesisType.SAME_PERSON
            )
            assert updated.id == hyp_id
            assert updated.reviewed_status == HypothesisReviewStatus.CONFIRMED.value
            assert updated.rules_version != "OLD_VERSION"  # обновлён до текущего
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dedup_to_hypothesis_pipeline(app_client, postgres_dsn) -> None:
    """bulk_compute_for_dedup_suggestions делает full conversion."""
    from parser_service.services.hypothesis_runner import (
        bulk_compute_for_dedup_suggestions,
    )
    from shared_models.orm import Hypothesis
    from sqlalchemy import func, select

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            count = await bulk_compute_for_dedup_suggestions(session, tree_id, min_confidence=0.50)
            await session.commit()

        assert count >= 1, "expected at least one hypothesis from dedup pipeline"

        async with SessionMaker() as session:
            total = await session.scalar(
                select(func.count(Hypothesis.id)).where(Hypothesis.tree_id == tree_id)
            )
            assert total == count
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_domain_entity_mutations(app_client, postgres_dsn) -> None:
    """READ-ONLY contract: hypothesis_runner не мутирует persons/sources/places."""
    from parser_service.services.hypothesis_runner import compute_hypothesis
    from shared_models.enums import HypothesisType
    from shared_models.orm import Person, Place, Source
    from sqlalchemy import func, select

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            counts_before = {
                "persons": await session.scalar(
                    select(func.count(Person.id)).where(Person.tree_id == tree_id)
                ),
                "sources": await session.scalar(
                    select(func.count(Source.id)).where(Source.tree_id == tree_id)
                ),
                "places": await session.scalar(
                    select(func.count(Place.id)).where(Place.tree_id == tree_id)
                ),
            }
            persons = (
                (await session.execute(select(Person).where(Person.tree_id == tree_id)))
                .scalars()
                .all()
            )
            i1, i2 = persons[0], persons[1]
            await compute_hypothesis(session, tree_id, i1.id, i2.id, HypothesisType.SAME_PERSON)
            await session.commit()

        async with SessionMaker() as session:
            counts_after = {
                "persons": await session.scalar(
                    select(func.count(Person.id)).where(Person.tree_id == tree_id)
                ),
                "sources": await session.scalar(
                    select(func.count(Source.id)).where(Source.tree_id == tree_id)
                ),
                "places": await session.scalar(
                    select(func.count(Place.id)).where(Place.tree_id == tree_id)
                ),
            }
        assert counts_before == counts_after, (
            f"hypothesis_runner must not mutate domain entities: "
            f"before={counts_before}, after={counts_after}"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_compute_returns_none_on_missing_subject(app_client, postgres_dsn) -> None:
    """Несуществующий subject_id → None (not crash)."""
    from parser_service.services.hypothesis_runner import compute_hypothesis
    from shared_models.enums import HypothesisType
    from shared_models.orm import Person
    from sqlalchemy import select

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            persons = (
                (await session.execute(select(Person).where(Person.tree_id == tree_id)))
                .scalars()
                .all()
            )
            i1 = persons[0]
            ghost = uuid.uuid4()
            result = await compute_hypothesis(
                session, tree_id, i1.id, ghost, HypothesisType.SAME_PERSON
            )
            assert result is None
    finally:
        await engine.dispose()
