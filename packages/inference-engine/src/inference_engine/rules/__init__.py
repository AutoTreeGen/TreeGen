"""InferenceRule plug-ins для inference-engine.

Phase 7.0 ships только ``BirthYearMatchRule`` как demo plugin
architecture. Phase 7.1+ добавит surname (Daitch-Mokotoff), place
hierarchy, parent-age sanity, sex match, DNA segment evidence rule.
"""

from inference_engine.rules.base import InferenceRule
from inference_engine.rules.birth_year_match import BirthYearMatchRule
from inference_engine.rules.registry import (
    RuleAlreadyRegisteredError,
    RuleNotFoundError,
    all_rules,
    clear_registry,
    get_rule,
    register_rule,
    unregister_rule,
)

__all__ = [
    "BirthYearMatchRule",
    "InferenceRule",
    "RuleAlreadyRegisteredError",
    "RuleNotFoundError",
    "all_rules",
    "clear_registry",
    "get_rule",
    "register_rule",
    "unregister_rule",
]
