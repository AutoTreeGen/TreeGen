"""InferenceRule plug-ins для inference-engine.

Phase 7.0 — framework (Hypothesis / Evidence / InferenceRule Protocol /
registry / composer).

Phase 7.1 добавил GEDCOM-rules:

* ``BirthYearMatchRule`` — proximity дат рождения (tiers 0 / ±1–2 / ≥10).
* ``SurnameMatchRule`` — Daitch-Mokotoff bucket overlap, с опциональной
  кириллической транслитерацией.
* ``BirthPlaceMatchRule`` — place_match_score из entity-resolution,
  с префиксным boost'ом для иерархии «Slonim» ⊂ «Slonim, Grodno».
* ``SexConsistencyRule`` — hard contradiction для same_person при
  несовпадении известных M/F.

Phase 7.3 добавил DNA-rule:

* ``DnaSegmentRelationshipRule`` — pairwise total-cM rule для гипотез
  ``same_person`` / ``parent_child`` / ``siblings`` с endogamy
  adjustment по ``EthnicityPopulation``. ADR-0023.

Все rule'ы — pure functions, тестируются синтетически (см. ADR-0016).
LLM-rules — Phase 10, отдельным пакетом с явным seed.
"""

from inference_engine.rules.base import InferenceRule
from inference_engine.rules.birth_year import BirthYearMatchRule
from inference_engine.rules.dna import DnaSegmentRelationshipRule
from inference_engine.rules.llm_place import LlmPlaceMatchRule, PlaceNormalizer
from inference_engine.rules.place import BirthPlaceMatchRule
from inference_engine.rules.registry import (
    RuleAlreadyRegisteredError,
    RuleNotFoundError,
    all_rules,
    clear_registry,
    get_rule,
    register_rule,
    unregister_rule,
)
from inference_engine.rules.sex import SexConsistencyRule
from inference_engine.rules.surname import SurnameMatchRule

__all__ = [
    "BirthPlaceMatchRule",
    "BirthYearMatchRule",
    "DnaSegmentRelationshipRule",
    "InferenceRule",
    "LlmPlaceMatchRule",
    "PlaceNormalizer",
    "RuleAlreadyRegisteredError",
    "RuleNotFoundError",
    "SexConsistencyRule",
    "SurnameMatchRule",
    "all_rules",
    "clear_registry",
    "get_rule",
    "register_rule",
    "unregister_rule",
]
