"""AutoTreeGen inference-engine — hypothesis-aware evidence composition (Phase 7.0).

Public API:

- ``Hypothesis``, ``Evidence``, ``HypothesisType``, ``EvidenceDirection`` —
  core Pydantic types.
- ``InferenceRule`` — Protocol for rule plug-ins.
- ``register_rule``, ``unregister_rule``, ``get_rule``, ``all_rules``,
  ``clear_registry`` — registry helpers.
- ``RuleAlreadyRegisteredError``, ``RuleNotFoundError`` — registry exceptions.
- ``compose_hypothesis`` — main entry point: apply rules, aggregate evidences,
  compute composite score.
- ``aggregate_confidence`` / ``AggregatedConfidence`` / ``SourceContribution``
  — Phase 7.5 confidence aggregation v2 (Bayesian fusion + contradictions),
  см. ADR-0065. Доступно отдельно для caller'ов, которые хотят пересчитать
  score из persisted evidences без re-running rules.
- ``ego_relations`` — Phase 10.7a ego-relationship resolver. Pure-function
  BFS по структуре дерева, возвращающий kind/degree/via/twin-flag для
  пары (ego, target). Вход — ``FamilyTraversal`` snapshot, заполняемый
  caller'ом из БД. См. ADR-0068.

См. README.md, docs/adr/0016-inference-engine-architecture.md, ADR-0065,
ADR-0068.
"""

from inference_engine.aggregation import (
    AggregatedConfidence,
    SourceContribution,
    aggregate_confidence,
)
from inference_engine.composer import compose_hypothesis
from inference_engine.rules.base import InferenceRule
from inference_engine.rules.registry import (
    RuleAlreadyRegisteredError,
    RuleNotFoundError,
    all_rules,
    clear_registry,
    get_rule,
    register_rule,
    unregister_rule,
)
from inference_engine.types import (
    Evidence,
    EvidenceDirection,
    Hypothesis,
    HypothesisType,
)

__all__ = [
    "AggregatedConfidence",
    "Evidence",
    "EvidenceDirection",
    "Hypothesis",
    "HypothesisType",
    "InferenceRule",
    "RuleAlreadyRegisteredError",
    "RuleNotFoundError",
    "SourceContribution",
    "aggregate_confidence",
    "all_rules",
    "clear_registry",
    "compose_hypothesis",
    "get_rule",
    "register_rule",
    "unregister_rule",
]
