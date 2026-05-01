"""Тесты DnaSegmentRelationshipRule (Phase 7.3 / ADR-0023).

Покрытие:
    - Базовые SUPPORTS / CONTRADICTS ветки для same_person / parent_child /
      siblings.
    - Шум-floor 7 cM.
    - Серая зона между full-sibling и parent-child (silent).
    - Endogamy adjustment делит weight на multiplier.
    - Гипотезы вне поддержанного набора (marriage / duplicate_*) → silent.
    - Отсутствие или некорректный context["dna_evidence"] → silent.
    - Composer integration: DNA-evidence комбинируется с GEDCOM-rules
      и поднимает composite_score.
"""

from __future__ import annotations

import math

from inference_engine import (
    EvidenceDirection,
    HypothesisType,
    compose_hypothesis,
)
from inference_engine.rules.birth_year import BirthYearMatchRule
from inference_engine.rules.dna import DnaSegmentRelationshipRule
from inference_engine.rules.surname import SurnameMatchRule

_RULE = DnaSegmentRelationshipRule()


def _ctx(
    hypothesis_type: str,
    *,
    total_cm: float | None = None,
    longest_segment_cm: float = 0.0,
    segment_count: int = 0,
    pop_a: str = "general",
    pop_b: str = "general",
    source: str = "computed_pairwise",
    kit_id_a: str | None = None,
    kit_id_b: str | None = None,
    omit_dna: bool = False,
) -> dict[str, object]:
    """Собрать context-dict в shape, который ожидает rule."""
    ctx: dict[str, object] = {"hypothesis_type": hypothesis_type}
    if not omit_dna and total_cm is not None:
        ctx["dna_evidence"] = {
            "total_cm": total_cm,
            "longest_segment_cm": longest_segment_cm,
            "segment_count": segment_count,
            "ethnicity_population_a": pop_a,
            "ethnicity_population_b": pop_b,
            "source": source,
            "kit_id_a": kit_id_a,
            "kit_id_b": kit_id_b,
        }
    return ctx


# ----- rule_id stability ----------------------------------------------------


def test_rule_id_is_stable() -> None:
    assert _RULE.rule_id == "dna_segment_relationship"


# ----- silent paths ---------------------------------------------------------


def test_unsupported_hypothesis_type_silent() -> None:
    """Marriage / duplicate_* / etc. → пустой list."""
    ctx = _ctx("marriage", total_cm=2700)
    assert _RULE.apply({}, {}, ctx) == []
    ctx2 = _ctx("duplicate_source", total_cm=2700)
    assert _RULE.apply({}, {}, ctx2) == []


def test_missing_dna_evidence_silent() -> None:
    ctx = _ctx("same_person", omit_dna=True)
    assert _RULE.apply({}, {}, ctx) == []


def test_dna_evidence_not_a_dict_silent() -> None:
    ctx: dict[str, object] = {
        "hypothesis_type": "same_person",
        "dna_evidence": "not-a-dict",
    }
    assert _RULE.apply({}, {}, ctx) == []


def test_total_cm_below_noise_floor_silent() -> None:
    """< 7 cM — шум по ADR-0014, rule не должен ничего возвращать."""
    ctx = _ctx("same_person", total_cm=6.5)
    assert _RULE.apply({}, {}, ctx) == []


def test_total_cm_missing_silent() -> None:
    ctx: dict[str, object] = {
        "hypothesis_type": "parent_child",
        "dna_evidence": {"longest_segment_cm": 50.0},  # нет total_cm
    }
    assert _RULE.apply({}, {}, ctx) == []


def test_total_cm_invalid_type_silent() -> None:
    ctx: dict[str, object] = {
        "hypothesis_type": "parent_child",
        "dna_evidence": {"total_cm": "not-a-number"},
    }
    assert _RULE.apply({}, {}, ctx) == []


# ----- SAME_PERSON ----------------------------------------------------------


def test_same_person_supports_at_identical_twin_threshold() -> None:
    """≥ 3 400 cM — identical twin / self range."""
    ctx = _ctx("same_person", total_cm=3500.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.SUPPORTS
    assert math.isclose(ev.weight, 0.85)
    assert "identical-twin" in ev.observation
    assert ev.source_provenance["total_cm"] == 3500.0
    assert ev.source_provenance["endogamy_multiplier"] == 1.0


def test_same_person_contradicts_below_full_sibling_lower_bound() -> None:
    """< 1 500 cM — точно не один и тот же человек."""
    ctx = _ctx("same_person", total_cm=1200.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.CONTRADICTS
    assert math.isclose(ev.weight, 0.85)
    assert "below full-sibling" in ev.observation


def test_same_person_grey_zone_silent() -> None:
    """1500 ≤ total < 3400 — overlapping диапазоны parent / sibling. Silent."""
    ctx = _ctx("same_person", total_cm=2500.0)
    assert _RULE.apply({}, {}, ctx) == []


# ----- PARENT_CHILD ---------------------------------------------------------


def test_parent_child_supports_in_range() -> None:
    """2 376 ≤ total ≤ 3 720 — parent-child SUPPORTS."""
    ctx = _ctx("parent_child", total_cm=2700.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.SUPPORTS
    assert math.isclose(ev.weight, 0.80)
    assert "parent-child range" in ev.observation


def test_parent_child_supports_at_lower_boundary() -> None:
    ctx = _ctx("parent_child", total_cm=2376.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.SUPPORTS


def test_parent_child_supports_at_upper_boundary() -> None:
    ctx = _ctx("parent_child", total_cm=3720.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.SUPPORTS


def test_parent_child_contradicts_in_twin_zone() -> None:
    """> 3 800 cM — twin/same person zone, parent-child CONTRADICTS."""
    ctx = _ctx("parent_child", total_cm=3850.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.CONTRADICTS
    assert math.isclose(ev.weight, 0.70)
    assert "identical-twin" in ev.observation


def test_parent_child_contradicts_when_too_low() -> None:
    """< 1 500 cM — clearly distant, parent-child CONTRADICTS."""
    ctx = _ctx("parent_child", total_cm=800.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.CONTRADICTS
    assert math.isclose(ev.weight, 0.70)
    assert "too low" in ev.observation


def test_parent_child_grey_zone_between_sibling_and_parent_silent() -> None:
    """Серая зона 1500–2376 cM — sibling/aunt zone; silent для parent_child."""
    ctx = _ctx("parent_child", total_cm=2000.0)
    assert _RULE.apply({}, {}, ctx) == []


# ----- SIBLINGS -------------------------------------------------------------


def test_siblings_supports_in_full_sibling_range() -> None:
    """1 613 ≤ total ≤ 3 488 — full-sibling SUPPORTS."""
    ctx = _ctx("siblings", total_cm=2200.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.SUPPORTS
    assert math.isclose(ev.weight, 0.65)
    assert "full-sibling range" in ev.observation


def test_siblings_contradicts_below_threshold() -> None:
    """< 1 000 cM — clearly не full-sibling."""
    ctx = _ctx("siblings", total_cm=600.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.CONTRADICTS
    assert math.isclose(ev.weight, 0.60)


def test_siblings_above_full_sibling_range_silent() -> None:
    """Выше full-sibling — twin/parent zone; silent (overlap с parent_child)."""
    ctx = _ctx("siblings", total_cm=3600.0)
    assert _RULE.apply({}, {}, ctx) == []


# ----- Endogamy adjustment --------------------------------------------------


def test_ashkenazi_endogamy_reduces_weight() -> None:
    """AJ multiplier 1.6 → weight ≈ base / 1.6."""
    ctx = _ctx("same_person", total_cm=3500.0, pop_a="ashkenazi", pop_b="ashkenazi")
    [ev] = _RULE.apply({}, {}, ctx)
    assert math.isclose(ev.weight, 0.85 / 1.6, abs_tol=1e-6)
    assert ev.source_provenance["endogamy_multiplier"] == 1.6


def test_max_multiplier_used_for_mixed_pair() -> None:
    """Если один subject AJ, другой general — берём max(1.6, 1.0) = 1.6."""
    ctx = _ctx("parent_child", total_cm=2700.0, pop_a="ashkenazi", pop_b="general")
    [ev] = _RULE.apply({}, {}, ctx)
    assert math.isclose(ev.weight, 0.80 / 1.6, abs_tol=1e-6)


def test_amish_strong_endogamy() -> None:
    """Amish multiplier 2.0 → большее weight reduction."""
    ctx = _ctx("siblings", total_cm=2200.0, pop_a="amish", pop_b="amish")
    [ev] = _RULE.apply({}, {}, ctx)
    assert math.isclose(ev.weight, 0.65 / 2.0, abs_tol=1e-6)


def test_unknown_population_falls_back_to_general() -> None:
    """Незнакомое имя популяции → multiplier 1.0 (как general)."""
    ctx = _ctx("same_person", total_cm=3500.0, pop_a="unknown_cohort", pop_b="general")
    [ev] = _RULE.apply({}, {}, ctx)
    assert math.isclose(ev.weight, 0.85)
    assert ev.source_provenance["endogamy_multiplier"] == 1.0


def test_endogamy_does_not_change_direction() -> None:
    """Высокий multiplier снижает weight, но не переклассифицирует direction.

    ADR-0023 §«Endogamy adjustment»: direction остаётся CONTRADICTS,
    даже если multiplier велик.
    """
    ctx = _ctx("same_person", total_cm=1200.0, pop_a="amish", pop_b="amish")
    [ev] = _RULE.apply({}, {}, ctx)
    assert ev.direction is EvidenceDirection.CONTRADICTS


# ----- Provenance contents --------------------------------------------------


def test_provenance_includes_aggregates_no_raw_data() -> None:
    """source_provenance несёт total_cm, longest_segment, segment_count,
    multiplier, source attribution. raw rsids/genotypes отсутствуют — ADR-0012."""
    ctx = _ctx(
        "parent_child",
        total_cm=2700.0,
        longest_segment_cm=180.5,
        segment_count=22,
        kit_id_a="kit-a-uuid",
        kit_id_b="kit-b-uuid",
        source="ancestry_match_list",
    )
    [ev] = _RULE.apply({}, {}, ctx)
    prov = ev.source_provenance
    assert prov["total_cm"] == 2700.0
    assert prov["longest_segment_cm"] == 180.5
    assert prov["segment_count"] == 22
    assert prov["dna_source"] == "ancestry_match_list"
    assert prov["kit_id_a"] == "kit-a-uuid"
    assert prov["kit_id_b"] == "kit-b-uuid"
    assert "Shared cM Project 4.0" in prov["source"]
    # Никаких raw полей — provenance whitelist.
    raw_keys = {"rsid", "rsids", "genotype", "genotypes", "snp", "snps"}
    assert raw_keys.isdisjoint(prov.keys())


def test_provenance_kit_ids_omitted_when_none() -> None:
    """kit_id_* отсутствуют в provenance, если не предоставлены."""
    ctx = _ctx("siblings", total_cm=2200.0)
    [ev] = _RULE.apply({}, {}, ctx)
    assert "kit_id_a" not in ev.source_provenance
    assert "kit_id_b" not in ev.source_provenance


# ----- Composer integration -------------------------------------------------


def test_composer_combines_dna_with_gedcom_rules() -> None:
    """compose_hypothesis с DNA + name + year → composite поднимается выше
    GEDCOM-only baseline.

    Сравниваем два прогона: один без DNA, второй с parent-child DNA.
    Ожидаем явный bump composite_score.
    """
    subject_parent = {
        "given": "Vladimir",
        "surname": "Zhitnitzky",
        "birth_year": 1920,
    }
    subject_child = {
        "given": "Boris",
        "surname": "Zhitnitzky",
        "birth_year": 1948,
    }

    rules = [
        SurnameMatchRule(),
        BirthYearMatchRule(),
        DnaSegmentRelationshipRule(),
    ]

    # Без DNA — только surname (year-diff = 28 → серая зона).
    h_without_dna = compose_hypothesis(
        hypothesis_type=HypothesisType.PARENT_CHILD,
        subject_a=subject_parent,
        subject_b=subject_child,
        context={"hypothesis_type": "parent_child"},
        rules=rules,
    )
    # С DNA в parent-child range.
    h_with_dna = compose_hypothesis(
        hypothesis_type=HypothesisType.PARENT_CHILD,
        subject_a=subject_parent,
        subject_b=subject_child,
        context={
            "hypothesis_type": "parent_child",
            "dna_evidence": {
                "total_cm": 3400.0,
                "longest_segment_cm": 280.0,
                "segment_count": 22,
                "ethnicity_population_a": "general",
                "ethnicity_population_b": "general",
                "source": "computed_pairwise",
            },
        },
        rules=rules,
    )

    assert h_with_dna.composite_score > h_without_dna.composite_score
    # DNA-rule должен присутствовать в evidences.
    rule_ids_with_dna = {ev.rule_id for ev in h_with_dna.evidences}
    assert "dna_segment_relationship" in rule_ids_with_dna
    # GEDCOM-only прогон не содержит DNA-evidence.
    rule_ids_without_dna = {ev.rule_id for ev in h_without_dna.evidences}
    assert "dna_segment_relationship" not in rule_ids_without_dna


def test_composer_dna_contradiction_can_pull_score_down() -> None:
    """DNA CONTRADICTS снижает score (Phase 7.5: фиксированный штраф 0.1 за evidence).

    Phase 7.5 (ADR-0065): contradictions используют флэт-штраф независимо
    от веса. surname SUPPORTS 0.5 → fused 0.5; одно DNA CONTRADICTS → −0.1;
    итог 0.4. Это сильно ниже SAME_PERSON threshold (0.75), что и нужно
    для тестируемого свойства «strong DNA contradiction can pull score down».
    Trade-off: Phase 7.0–7.4 вычитал DNA weight (0.85) напрямую и обнулял
    score; Phase 7.5 более «мягкий» по дизайну. ADR-0065 §«Consequences»
    отмечает это как known characteristic — последующая ручная review всё
    равно увидит CONTRADICTS-evidence через ``contradiction_flags``.
    """
    rules = [SurnameMatchRule(), DnaSegmentRelationshipRule()]
    h = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={"surname": "Zhitnitzky"},
        subject_b={"surname": "Zhitnitzky"},
        context={
            "hypothesis_type": "same_person",
            "dna_evidence": {
                "total_cm": 800.0,  # < 1500 → CONTRADICTS 0.85
                "longest_segment_cm": 50.0,
                "segment_count": 5,
                "ethnicity_population_a": "general",
                "ethnicity_population_b": "general",
                "source": "computed_pairwise",
            },
        },
        rules=rules,
    )
    # surname SUPPORTS 0.5 (fused alone) − 0.1 penalty = 0.4.
    assert abs(h.composite_score - 0.4) < 1e-9
