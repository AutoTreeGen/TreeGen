"""Phase 7.3.1: DnaSegmentRelationshipRule зарегистрирован в hypothesis_runner.

Покрывает интеграцию ADR-0023 в runner:

* Rule присутствует в `_DEFAULT_RULE_CLASSES` → попадает в rules_version hash.
* `_load_dna_aggregate` находит DnaMatch для пары persons и собирает
  context-aggregate в формате, который ожидает rule.
* Финальный Hypothesis содержит evidence с rule_id=`dna_segment_relationship`
  и SUPPORTS-direction для парного cM в parent-child диапазоне.

Negative path (нет linked kit'ов → DNA-rule silent → Hypothesis собирается
только из GEDCOM-rules) уже неявно покрыт существующими hypothesis_runner
тестами — re-tests we don't repeat.
"""

from __future__ import annotations

import uuid

import pytest
from shared_models.enums import EthnicityPopulation, HypothesisType
from shared_models.orm import DnaKit, DnaMatch, Person, Tree, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


async def _seed_dna_pair(
    postgres_dsn: str,
    *,
    total_cm: float,
    ethnicity: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Создать tree + 2 persons + DnaKit (на person_a) + DnaMatch (на person_b).

    Returns: ``(tree_id, person_a_id, person_b_id)``.
    """
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    try:
        async with factory() as session, session.begin():
            user = User(
                email=f"dna-rule-{suffix}@example.com",
                external_auth_id=f"auth0|dna-rule-{suffix}",
                display_name="DNA Rule Test User",
            )
            session.add(user)
            await session.flush()

            tree = Tree(owner_user_id=user.id, name=f"DNA Rule Tree {suffix}")
            session.add(tree)
            await session.flush()

            person_a = Person(tree_id=tree.id, sex="M")
            person_b = Person(tree_id=tree.id, sex="M")
            session.add_all([person_a, person_b])
            await session.flush()

            kit = DnaKit(
                tree_id=tree.id,
                owner_user_id=user.id,
                person_id=person_a.id,
                source_platform="ancestry",
                external_kit_id=f"ext-{suffix}",
                display_name=f"DNA Rule Kit {suffix}",
                ethnicity_population=ethnicity,
            )
            session.add(kit)
            await session.flush()

            match = DnaMatch(
                tree_id=tree.id,
                kit_id=kit.id,
                external_match_id=f"match-{suffix}",
                display_name="Synthetic Match",
                total_cm=total_cm,
                largest_segment_cm=180.0,
                segment_count=24,
                matched_person_id=person_b.id,
            )
            session.add(match)

            result = (tree.id, person_a.id, person_b.id)
    finally:
        await engine.dispose()
    return result


@pytest.mark.asyncio
async def test_dna_rule_registered_emits_evidence_for_parent_child(
    postgres_dsn: str,
) -> None:
    """compute_hypothesis(PARENT_CHILD) на pair с total_cm в range emits DNA evidence.

    2800 cM попадает в parent-child SUPPORTS-диапазон 2376–3720 (ADR-0023).
    Endogamy=ashkenazi (×1.6) уменьшает weight, но direction остаётся SUPPORTS.
    """
    from parser_service.services.hypothesis_runner import compute_hypothesis

    tree_id, person_a_id, person_b_id = await _seed_dna_pair(
        postgres_dsn,
        total_cm=2800.0,
        ethnicity=EthnicityPopulation.ASHKENAZI.value,
    )

    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            hyp = await compute_hypothesis(
                session,
                tree_id,
                person_a_id,
                person_b_id,
                HypothesisType.PARENT_CHILD,
            )
            await session.commit()

            assert hyp is not None
            evidences_by_rule = {ev.rule_id: ev for ev in hyp.evidences}
            assert "dna_segment_relationship" in evidences_by_rule, (
                f"DNA rule not invoked; got rule_ids={list(evidences_by_rule)}"
            )

            dna_ev = evidences_by_rule["dna_segment_relationship"]
            assert dna_ev.direction == "supports"
            assert 0.0 < dna_ev.weight <= 1.0
            # Endogamy ÷1.6: 0.80 / 1.6 = 0.50 (sanity, не точное равенство).
            assert dna_ev.weight < 0.80, f"endogamy multiplier not applied: weight={dna_ev.weight}"

            # Provenance должен нести cM-агрегат, без raw genotypes.
            provenance = dna_ev.source_provenance
            assert provenance.get("total_cm") == pytest.approx(2800.0)
            assert "endogamy_multiplier" in provenance
    finally:
        await engine.dispose()


def test_dna_rule_in_default_pack() -> None:
    """Sanity: rule зарегистрирован в `_DEFAULT_RULE_CLASSES` и попадает в rules_version.

    Без БД — чистая проверка регистрации (rules_version меняется при
    добавлении нового rule, hypotheses со старым version помечаются
    stale, см. ADR-0021).
    """
    from parser_service.services.hypothesis_runner import (
        _DEFAULT_RULE_CLASSES,
        _compute_rules_version,
    )

    assert "dna_segment_relationship" in {cls.rule_id for cls in _DEFAULT_RULE_CLASSES}

    version = _compute_rules_version()
    assert version.startswith("engine=")
    assert ";rules=" in version
