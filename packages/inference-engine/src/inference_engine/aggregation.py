"""Confidence aggregation v2 (Phase 7.5).

Заменяет линейный weighted-sum (старый ``_composite_score`` в
``composer.py``) на разделение независимых и коррелированных
сигналов:

* **Bayesian fusion** для evidence из *разных* источников (разные
  ``rule_id``): ``P = 1 − Π(1 − p_i)``. Каждое supporting-доказательство
  сдвигает posterior вверх, но возврат уменьшается с количеством;
  на бесконечности → 1.0.
* **Same-source corroboration** для evidence с одним и тем же
  ``rule_id``: weighted average. Не Bayesian — output одного и того же
  правила на одних и тех же субъектах сильно коррелирован, naive fusion
  переоценивает уверенность («surname matched twice» ≠ «surname matched
  + birth-year matched»).
* **Contradictions** (CONTRADICTS): фиксированный штраф 0.1 за каждое
  противоречащее evidence, capped at 0.5. Не привязан к ``weight``,
  потому что качественный сигнал «контр-факт present» важнее, чем его
  численный вес: один реальный CONTRADICTS почти всегда означает
  серьёзную проблему с гипотезой, два — почти точно её опровергают.
* **Floor 0.05** (минимум при наличии хоть какого-то evidence): если
  есть хотя бы одно non-contradicting наблюдение, итог не сбрасывается
  в 0.0. «Знаем что-то» ≠ «ничего не знаем» — для UI это разные
  состояния (показать карточку vs скрыть).

Пустой ``evidence_list`` → ``composite_score = 0.0`` (особый случай,
floor не применяется — нет данных вообще).

Производительность: O(n) по числу evidence, без аллокаций кроме одного
``defaultdict``. На бенчмарках < 50 µs для 10 evidence (target из
spec — < 1 ms per hypothesis).

См. ADR-0065.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from inference_engine.types import Evidence, EvidenceDirection

# Штраф за одно CONTRADICTS-evidence. Не связан с weight — см. модуль
# docstring §«Contradictions». Подбирался эмпирически: 0.05 слишком
# мягкий (5 contradictions = -0.25, hypothesis ещё бодра), 0.2 жёсткий
# (одно случайное противоречие сразу обнуляет hypothesis).
CONTRADICTION_PENALTY_PER_EVIDENCE: Final[float] = 0.1

# Cap на суммарный штраф. После 5 CONTRADICTS дополнительные не
# усиливают сигнал — гипотеза уже считается слабой, дальше шум.
CONTRADICTION_PENALTY_CAP: Final[float] = 0.5

# Floor — минимальное значение composite_score при наличии evidence.
# Нужен чтобы UI отличал «есть данные, но слабые» от «вообще нет данных».
MIN_CONFIDENCE_WITH_EVIDENCE: Final[float] = 0.05


class SourceContribution(BaseModel):
    """Вклад одного источника (rule_id) в composite_score.

    Attributes:
        rule_id: Идентификатор правила-источника.
        evidence_count: Сколько evidence-rows этого правила учтено.
        aggregated_weight: Weighted-average весов SUPPORTS внутри группы.
            Это та «вероятность», с которой источник входит в Bayesian
            fusion. Для группы только из NEUTRAL — 0.0 (источник
            существует, но score не двигает).
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    evidence_count: int = Field(ge=0)
    aggregated_weight: float = Field(ge=0.0, le=1.0)


class AggregatedConfidence(BaseModel):
    """Результат aggregation: composite + breakdown + flags.

    Attributes:
        composite_score: Финальный confidence в ``[0, 1]``. Сохраняется
            в ``Hypothesis.composite_score`` (контракт ORM не меняется).
        source_breakdown: Список вкладов по rule_id, отсортированный по
            ``aggregated_weight`` DESC. UI использует для explanation
            («surname rule contributed 0.7, birth-year — 0.3»).
        contradiction_flags: Список ``rule_id`` правил, выпустивших хотя
            бы одно CONTRADICTS-evidence. Не дублирует penalty (тот уже
            в composite_score) — это для UI «warning chips».
        contradiction_penalty: Сколько было вычтено в итоге; полезно для
            audit / debug. ≥ 0, capped по ``CONTRADICTION_PENALTY_CAP``.
    """

    model_config = ConfigDict(frozen=True)

    composite_score: float = Field(ge=0.0, le=1.0)
    source_breakdown: list[SourceContribution] = Field(default_factory=list)
    contradiction_flags: list[str] = Field(default_factory=list)
    contradiction_penalty: float = Field(ge=0.0, le=CONTRADICTION_PENALTY_CAP)


def aggregate_confidence(evidence_list: list[Evidence]) -> AggregatedConfidence:
    """Посчитать composite-confidence по списку evidence.

    Алгоритм (см. модульный docstring):

    1. Группировка SUPPORTS по ``rule_id`` → weighted-average внутри
       группы (weight ≈ self-weight, поэтому это просто mean of weights;
       выбор сохранён симметричным на случай если weighting усложнится).
    2. Bayesian fusion групп: ``1 − Π(1 − w_g)``.
    3. Subtract contradiction penalty.
    4. Apply floor 0.05 если есть хоть какое-то evidence.

    Edge cases:

    * ``evidence_list = []`` → ``composite_score = 0.0`` (no evidence
      means no claim; floor не применяется).
    * Только NEUTRAL evidence → ``composite_score = 0.05`` (floor:
      существование наблюдения ≠ нет данных).
    * Только CONTRADICTS → ``composite_score = 0.05`` (floor: гипотеза
      не доказана и опровергается, но мы её всё-таки рассматривали).
    """
    if not evidence_list:
        return AggregatedConfidence(
            composite_score=0.0,
            source_breakdown=[],
            contradiction_flags=[],
            contradiction_penalty=0.0,
        )

    # Группировка SUPPORTS по rule_id.
    supports_by_rule: dict[str, list[float]] = defaultdict(list)
    contradiction_rules: list[str] = []
    contradiction_count = 0
    seen_contradiction_rules: set[str] = set()

    for ev in evidence_list:
        if ev.direction is EvidenceDirection.SUPPORTS:
            supports_by_rule[ev.rule_id].append(ev.weight)
        elif ev.direction is EvidenceDirection.CONTRADICTS:
            contradiction_count += 1
            if ev.rule_id not in seen_contradiction_rules:
                seen_contradiction_rules.add(ev.rule_id)
                contradiction_rules.append(ev.rule_id)
        # NEUTRAL: видим evidence (для floor), но score не двигаем.

    # Per-source weighted-average. Для одной группы со списком
    # [w1, w2, ...] это просто среднее — все evidence равноценны
    # внутри одного rule_id. Phase 7.6+ может ввести per-evidence
    # confidence-weights и это станет настоящим weighted average.
    contributions: list[SourceContribution] = []
    for rule_id, weights in supports_by_rule.items():
        avg = sum(weights) / len(weights)
        contributions.append(
            SourceContribution(
                rule_id=rule_id,
                evidence_count=len(weights),
                aggregated_weight=avg,
            )
        )

    # Bayesian fusion: 1 − Π(1 − w_g).
    if contributions:
        product_complement = 1.0
        for c in contributions:
            product_complement *= 1.0 - c.aggregated_weight
        bayesian = 1.0 - product_complement
    else:
        bayesian = 0.0

    # Контрадикции: 0.1 за штуку, cap 0.5.
    raw_penalty = CONTRADICTION_PENALTY_PER_EVIDENCE * contradiction_count
    penalty = min(raw_penalty, CONTRADICTION_PENALTY_CAP)

    # Применяем штраф и зажимаем в [0, 1].
    score = max(0.0, min(1.0, bayesian - penalty))

    # Floor: если у нас вообще что-то наблюдалось — не падаем в 0.
    score = max(score, MIN_CONFIDENCE_WITH_EVIDENCE)

    contributions.sort(key=lambda c: c.aggregated_weight, reverse=True)

    return AggregatedConfidence(
        composite_score=score,
        source_breakdown=contributions,
        contradiction_flags=contradiction_rules,
        contradiction_penalty=penalty,
    )


__all__ = [
    "CONTRADICTION_PENALTY_CAP",
    "CONTRADICTION_PENALTY_PER_EVIDENCE",
    "MIN_CONFIDENCE_WITH_EVIDENCE",
    "AggregatedConfidence",
    "SourceContribution",
    "aggregate_confidence",
]
