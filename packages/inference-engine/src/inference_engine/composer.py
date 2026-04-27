"""Hypothesis composition: применить rule's, агрегировать Evidence, посчитать score.

ADR-0016 §«Composer» — формула:

    score = clamp(Σ supports.weight − Σ contradicts.weight, 0, 1)

Это **не** Bayes posterior. В Phase 7.0 у нас нет prior'ов из дерева,
поэтому композиция — простая weighted sum. Phase 7.4+ может ввести
prior из tree-context и переключить формулу на posterior-style;
контракт InferenceRule при этом не меняется.

Корреляция между evidences не учитывается: два rule's, смотрящих на
один и тот же сигнал с разных углов, оба добавляют свой weight как
независимые факты. Это known limitation, документируется в ADR-0016
§«Минусы» — Mitigation планируется в Phase 7.5 (explicit correlation
matrix или Bayes-network).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from inference_engine.rules.registry import all_rules
from inference_engine.types import (
    Evidence,
    EvidenceDirection,
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
        и пустым ``alternatives`` (генерация альтернатив — Phase 7.4).
    """
    effective_context: dict[str, Any] = {} if context is None else context
    effective_rules: list[InferenceRule] = all_rules() if rules is None else rules

    evidences: list[Evidence] = []
    for rule in effective_rules:
        evidences.extend(rule.apply(subject_a, subject_b, effective_context))

    composite = _composite_score(evidences)

    return Hypothesis(
        id=uuid4(),
        hypothesis_type=hypothesis_type,
        subject_a_id=subject_a_id if subject_a_id is not None else uuid4(),
        subject_b_id=subject_b_id if subject_b_id is not None else uuid4(),
        evidences=evidences,
        composite_score=composite,
        alternatives=[],
    )


def _composite_score(evidences: list[Evidence]) -> float:
    """Weighted-sum формула с clamp в ``[0, 1]``.

    Supports добавляют свой weight, contradicts вычитают, neutral
    игнорируется в score (но виден в evidences-листе для UI).
    """
    supports_total = sum(
        ev.weight for ev in evidences if ev.direction is EvidenceDirection.SUPPORTS
    )
    contradicts_total = sum(
        ev.weight for ev in evidences if ev.direction is EvidenceDirection.CONTRADICTS
    )
    raw = supports_total - contradicts_total
    return max(0.0, min(1.0, raw))
