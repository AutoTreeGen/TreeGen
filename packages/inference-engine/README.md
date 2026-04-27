# inference-engine

Hypothesis-aware inference primitives for AutoTreeGen (Phase 7).

This package is the core of **evidence-based genealogy** in AutoTreeGen
(CLAUDE.md §3.2). What makes AutoTreeGen distinct from Ancestry,
MyHeritage, and FamilySearch is that every claim is a **Hypothesis**
backed by **Evidence** — supporting *and* contradicting — produced by
deterministic **InferenceRule**s. There are no opaque "Hint: this is
your ancestor" suggestions; every score is decomposable to its
components.

## Status (Phase 7.0)

Skeleton + first concrete rule. Capabilities:

- `Hypothesis`, `Evidence`, `HypothesisType`, `EvidenceDirection` —
  Pydantic models for the hypothesis graph.
- `InferenceRule` — `runtime_checkable` Protocol; any object with
  `rule_id` + `apply()` qualifies.
- `register_rule` / `get_rule` / `all_rules` / `clear_registry` —
  module-level rule registry.
- `compose_hypothesis()` — applies all (or a subset of) registered
  rules to a pair of subjects, aggregates Evidence, computes a
  weighted-sum composite score in `[0, 1]`.
- `BirthYearMatchRule` — first concrete rule (Phase 7.0 example).

Persistence (ORM), HTTP API, calibration tables, and additional rules
(Daitch-Mokotoff surname, place hierarchy match, DNA segment evidence,
parent-age sanity, …) ship in Phase 7.1+.

## Quickstart

```python
from inference_engine import (
    HypothesisType,
    compose_hypothesis,
    register_rule,
)
from inference_engine.rules import BirthYearMatchRule

register_rule(BirthYearMatchRule())

a = {"given": "Vladimir", "surname": "Zhitnitzky", "birth_year": 1945}
b = {"given": "Volodya",  "surname": "Жitницкий",  "birth_year": 1945}

hypothesis = compose_hypothesis(
    hypothesis_type=HypothesisType.SAME_PERSON,
    subject_a=a,
    subject_b=b,
    context={},
)

print(hypothesis.composite_score)  # 0.8
for evidence in hypothesis.evidences:
    print(f"  {evidence.direction.value:11s} {evidence.weight:.2f} {evidence.observation}")
```

## Design

See `docs/adr/0016-inference-engine-architecture.md` for the full
decision record. Key invariants:

- **Pure functions, no I/O.** Rules read `subject_a`, `subject_b`,
  `context` (all plain `dict`s), return `list[Evidence]`. No DB,
  HTTP, file access, or environment lookups inside a rule.
- **Counter-evidence is first-class.** `EvidenceDirection.CONTRADICTS`
  reduces composite score; `NEUTRAL` is recorded but does not move
  the score. "No data" and "data against" are distinct states.
- **Composite score ∈ [0, 1].** MVP formula is
  `clamp(Σ supports.weight − Σ contradicts.weight, 0, 1)`.
  This is **not** a Bayes posterior — there is no prior in Phase 7.0.
  Phase 7.4+ may introduce tree-context priors.
- **Plugin protocol.** Any object with attributes
  `rule_id: str` and `apply(subject_a, subject_b, context) -> list[Evidence]`
  qualifies via `runtime_checkable` Protocol. Inheritance is **not**
  required.

## Layout

```text
src/inference_engine/
  types.py             # Hypothesis, Evidence, HypothesisType, EvidenceDirection
  composer.py          # compose_hypothesis()
  rules/
    base.py            # InferenceRule Protocol
    registry.py        # register_rule / get_rule / all_rules / clear_registry
    birth_year_match.py # BirthYearMatchRule (Phase 7.0 example)
  py.typed
tests/
  test_types.py        # Pydantic validation, score clamping
  test_registry.py     # CRUD on the registry
  test_composer.py     # weighted-sum formula edge cases
  test_birth_year_match.py # rule-specific edge cases + integration test
```

## Privacy & determinism

- **Determinism.** Same inputs → same evidences → same composite score.
  No `random.random()`, no clock-dependent logic, no LLM calls.
  LLM-augmented rules (Phase 10) live in a separate package and slot
  into the same Protocol.
- **No raw PII in logs.** This package has no logging today; future
  rules that touch DNA / sensitive identifiers must follow ADR-0012
  privacy guards (aggregates only, no raw values).

## Related

- `docs/adr/0016-inference-engine-architecture.md` — design decisions.
- CLAUDE.md §3.1 (evidence-first), §3.2 (hypothesis-aware), §3.6
  (deterministic > magic).
- Future Phase 7.x: ORM persistence, HTTP API, alternative-hypothesis
  generation, calibration tables, more rules.
