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

См. README.md и docs/adr/0016-inference-engine-architecture.md.
"""

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
    "Evidence",
    "EvidenceDirection",
    "Hypothesis",
    "HypothesisType",
    "InferenceRule",
    "RuleAlreadyRegisteredError",
    "RuleNotFoundError",
    "all_rules",
    "clear_registry",
    "compose_hypothesis",
    "get_rule",
    "register_rule",
    "unregister_rule",
]
