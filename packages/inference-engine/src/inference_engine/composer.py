"""Hypothesis composition: применить rule's, агрегировать Evidence, посчитать score.

Phase 7.0–7.4 использовали линейную weighted-sum формулу
``clamp(Σ supports.weight − Σ contradicts.weight, 0, 1)`` (ADR-0016).
Two evidence на 0.6 → 1.0 (clamp), CONTRADICTS вычитал свой weight
напрямую, корреляция между rule's игнорировалась.

Phase 7.5 (см. ADR-0057) разделила сигналы:

* **Bayesian fusion** для разных ``rule_id``: ``1 − Π(1 − p_i)``.
* **Same-source corroboration** для одного ``rule_id``: weighted average.
* **Contradictions**: фиксированный штраф 0.1 за evidence, capped at 0.5.
* **Floor 0.05** при наличии хоть какого-то evidence.

Контракт ``Hypothesis`` остаётся прежним: ``composite_score: float``
в ``[0, 1]``. Вся новая логика — в ``aggregation.aggregate_confidence``;
composer лишь делегирует туда. ORM-схема, API и тестовые fixtures
не меняются.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from inference_engine.aggregation import aggregate_confidence
from inference_engine.rules.registry import all_rules
from inference_engine.types import (
    Evidence,
    Hypothesis,
    HypothesisType,
)

if TYPE_CHECKING:
    from inference_engine.rules.base import InferenceRule


def compose_hypothesis(
    hypothesis_type: HypothesisType,
    subject_a: dict[str, Any],
    subject_b: dict[str, Any],
    context: dict[str, Any] | None = None,
    rules: list[InferenceRule] | None = None,
    subject_a_id: UUID | None = None,
    subject_b_id: UUID | None = None,
) -> Hypothesis:
    """Применить rule's к паре subjects, собрать Evidence, посчитать composite score.

    Args:
        hypothesis_type: Тип гипотезы (SAME_PERSON, PARENT_CHILD, …).
        subject_a, subject_b: Сравниваемые сущности (произвольные dict'ы).
        context: Общий контекст для rule's. По умолчанию ``{}``.
        rules: Явный список rule's. Если ``None`` — берём все из registry
            через ``all_rules()``. Пустой список — допустимо, но даёт
            гипотезу с пустыми evidences и score=0.0.
        subject_a_id, subject_b_id: UUID сравниваемых сущностей. Если
            не переданы — генерируются ``uuid4()``. В Phase 7.2+ caller
            обязан передавать FK на персистентные ID.

    Returns:
        Hypothesis с собранными evidences, посчитанным composite_score
        (через ``aggregate_confidence``, см. ADR-0057) и пустым
        ``alternatives`` (генерация альтернатив — Phase 7.4).
    """
    effective_context: dict[str, Any] = {} if context is None else context
    effective_rules: list[InferenceRule] = all_rules() if rules is None else rules

    evidences: list[Evidence] = []
    for rule in effective_rules:
        evidences.extend(rule.apply(subject_a, subject_b, effective_context))

    aggregated = aggregate_confidence(evidences)

    return Hypothesis(
        id=uuid4(),
        hypothesis_type=hypothesis_type,
        subject_a_id=subject_a_id if subject_a_id is not None else uuid4(),
        subject_b_id=subject_b_id if subject_b_id is not None else uuid4(),
        evidences=evidences,
        composite_score=aggregated.composite_score,
        alternatives=[],
    )
