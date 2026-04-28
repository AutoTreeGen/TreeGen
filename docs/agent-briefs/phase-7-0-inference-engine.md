# Agent brief — Phase 7.0: inference-engine skeleton (hypothesis-aware)

> **Кому:** Агент 6 (Claude Code CLI, bypass on) — first task на TreeGen.
> **Контекст:** Windows, `D:\Projects\TreeGen`, default branch `main`.
> **Перед стартом:** обязательно прочитай `CLAUDE.md`, `ROADMAP.md`,
> `docs/architecture.md`, `docs/data-model.md`, `docs/adr/0008-ci-precommit-parity.md`.
> **Worktree:** рекомендую `git worktree add ../TreeGen-inference main` для
> изоляции от 5 параллельных агентов.

---

## Контекст — почему это важно

CLAUDE.md секция 3 (нерушимые архитектурные принципы):

> **(принцип 2)** **Hypothesis-aware.** Гипотезы — first-class entity, не
> «черновики». Хранятся с rationale и evidence-graph.

Inference engine — **сердце "evidence-based генеалогии" в AutoTreeGen.**
То что отличает AutoTreeGen от Ancestry/MyHeritage:

- Они показывают "Hint: this is your ancestor" — без объяснения
- AutoTreeGen строит **hypothesis graph**: гипотеза → evidence → score →
  альтернативные гипотезы → confidence

Пример use case:

```text
Hypothesis: Person A "Vladimir Zhitnitzky b.1945 Dnepropetrovsk"
            is the same as
            Person B "Volodya Жitницкий b.1945 Днепр"

Evidence:
  + Surname match (Daitch-Mokotoff bucket)        weight=0.3
  + Birth year exact                              weight=0.2
  + Place match ("Dnepropetrovsk" ≈ "Днепр")      weight=0.2
  + Given name diminutive (Volodya = Vladimir)    weight=0.15
  + No DNA conflict                               weight=0.15

Composite: 0.92  → Highly likely

Alternative hypotheses:
  - Same person (0.92)
  - Brothers/twins (0.05) — but no FAMC data confirms
  - Coincidence (0.03)
```

Это и есть **hypothesis-aware genealogy** — каждое утверждение имеет
evidence + counter-evidence + score.

**Phase 7.0 — скелет**, не полный алгоритм. Goal: structure готова
для Phase 7.1+ заполнения.

**Параллельно работают:**

- Агент 1: `apps/web/` (Phase 4.3)
- Агент 2: `packages/entity-resolution/` (Phase 3.4)
- Агент 3: `packages/familysearch-client/` (Phase 5.0)
- Агент 4: `packages/dna-analysis/` (Phase 6.1)
- Агент 5: `packages/gedcom-parser/` (Phase 1.x — после твоего start)

**Твоя территория** (нулевое пересечение):

- `packages/inference-engine/` — **новый/пустой пакет**, наполнить
- `docs/adr/0016-inference-engine-architecture.md` — новый ADR
- `docs/research/hypothesis-aware-genealogy.md` — новый research note
  (опциональный)

**Что НЕ трогай:**

- Все остальные packages/services/apps
- `packages/shared-models/orm.py` — не добавляй модели Hypothesis в ORM
  в этой Phase, только в-памяти structures. ORM models — Phase 7.2.

---

## Цель Phase 7.0

1. **ADR-0016** — архитектурное решение: что такое Hypothesis, Evidence,
   InferenceRule, как они композируются
2. **`packages/inference-engine/` scaffold** — Pydantic models +
   pure-function evidence aggregator + plugin protocol для inference rules
3. **Один rule example** — `SameSurnameRule` или `BirthYearMatchRule` —
   demo как rules плагинируются
4. **Тесты на синтетических данных** — все примеры из CLAUDE.md и моего
   контекста проверяются

**НЕ делать:**

- ORM persistence (Phase 7.2)
- HTTP API (Phase 7.3)
- LLM-augmented rules (Phase 10)
- Real entity-resolution integration (Phase 7.x)

---

## Задачи (в этом порядке)

### Task 1 — docs(adr): ADR-0016 inference engine architecture

**Цель:** зафиксировать дизайн до кода.

**Шаги:**

1. `git checkout main && git pull`
2. `git worktree add ../TreeGen-inference docs/adr-0016-inference-engine`
3. `cd ../TreeGen-inference`
4. Создать `docs/adr/0016-inference-engine-architecture.md`:
   - Status: Accepted, Date: today, Authors: @autotreegen
   - Tags: inference, hypothesis, evidence-graph, phase-7
   - Контекст: hypothesis-aware genealogy (CLAUDE.md §3.2)
   - Core concepts:
     - **Hypothesis** = claim о связи между entities ("A is parent of B",
       "A is same as C")
     - **Evidence** = atomic fact supporting/contradicting (или neutral)
       hypothesis with weight + source
     - **InferenceRule** = pure function (entity_a, entity_b, context) →
       list[Evidence]
     - **HypothesisGraph** = composition of evidences → composite score +
       alternatives
   - Architecture:
     - **Pure functions, no I/O** в core engine
     - **Plugin protocol** для rules — registered через entry point or
       explicit registry
     - **Composability:** rules могут зависеть от других rules
       (DNA evidence depends on segment matching from dna-analysis)
   - Рассмотренные варианты:
     - A. Pure-function rules + composition (рекомендую — testable, deterministic)
     - B. Bayesian network library (overkill для MVP, не deterministic)
     - C. Rule engine типа Drools / clips (не Python-native, дорого)
     - D. LLM-driven inference (Phase 10, не сейчас)
   - Решение: A
   - Persistence (когда Phase 7.2 будет): hypotheses → ORM с FK на
     evidence list, alternatives — separate table
   - Когда пересмотреть: появится >100 rules → нужен dependency graph;
     или захотим runtime rule editing
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR
7. Merge after green CI

### Task 2 — feat(inference-engine): scaffold + core types

**Цель:** Pydantic models + protocol для plug-in rules.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b feat/phase-7.0-inference-scaffold`
3. Проверить если `packages/inference-engine/` существует (per CLAUDE.md
   секция 4a). Создать/наполнить.
4. Структура:

   ```text
   packages/inference-engine/
     pyproject.toml          (deps: pydantic>=2.5)
     README.md
     src/inference_engine/
       __init__.py
       types.py              # Hypothesis, Evidence, EvidenceWeight enums
       rules/
         __init__.py
         base.py             # InferenceRule Protocol
         registry.py         # rule registry / plugin loader
       composer.py           # combine evidences -> hypothesis score
       py.typed
     tests/
       conftest.py
       test_types.py
       test_composer.py
       test_registry.py
   ```

5. `types.py`:

   ```python
   from typing import Literal
   from pydantic import BaseModel, Field
   from uuid import UUID

   class EvidenceDirection(str, Enum):
       SUPPORTS = "supports"
       CONTRADICTS = "contradicts"
       NEUTRAL = "neutral"

   class Evidence(BaseModel):
       rule_id: str           # which rule produced this
       direction: EvidenceDirection
       weight: float          # 0..1
       observation: str       # human-readable: "Birth year exact match"
       source_provenance: dict[str, Any] = Field(default_factory=dict)

   class HypothesisType(str, Enum):
       SAME_PERSON = "same_person"
       PARENT_CHILD = "parent_child"
       SIBLINGS = "siblings"
       MARRIAGE = "marriage"

   class Hypothesis(BaseModel):
       id: UUID                         # generated
       hypothesis_type: HypothesisType
       subject_a_id: UUID
       subject_b_id: UUID
       evidences: list[Evidence] = Field(default_factory=list)
       composite_score: float = 0.0     # computed by composer
       alternatives: list["Hypothesis"] = Field(default_factory=list)
   ```

6. `rules/base.py`:

   ```python
   from typing import Protocol, runtime_checkable

   @runtime_checkable
   class InferenceRule(Protocol):
       """Pure function: (subject_a, subject_b, context) -> list[Evidence]."""
       rule_id: str

       def apply(
           self,
           subject_a: dict,        # serialized entity, e.g. PersonForMatching
           subject_b: dict,
           context: dict,          # e.g. {"genetic_map": ..., "tree_id": ...}
       ) -> list[Evidence]: ...
   ```

7. `rules/registry.py`:

   ```python
   _registry: dict[str, InferenceRule] = {}

   def register_rule(rule: InferenceRule) -> None: ...
   def get_rule(rule_id: str) -> InferenceRule: ...
   def all_rules() -> list[InferenceRule]: ...
   ```

8. `composer.py`:

   ```python
   def compose_hypothesis(
       hypothesis_type: HypothesisType,
       subject_a: dict,
       subject_b: dict,
       context: dict,
       rules: list[InferenceRule] | None = None,  # default: all registered
   ) -> Hypothesis:
       """Apply all rules, aggregate evidences, compute composite score."""
   ```

   Composite formula (MVP): weighted sum of supporting evidences minus
   weighted sum of contradicting. Cap [0, 1].
9. Тесты:
   - test_evidence_pydantic_validation
   - test_hypothesis_with_zero_evidences_has_zero_score
   - test_supports_increase_score
   - test_contradicts_decrease_score
   - test_neutral_doesnt_change_score
   - test_register_and_lookup_rule
   - test_compose_with_no_registered_rules_yields_zero_score
10. `pwsh scripts/check.ps1` зелёное
11. Commit, push, PR

### Task 3 — feat(inference-engine): example rule + integration test

**Цель:** один реальный rule, доказывающий plugin architecture работает.

**Шаги:**

1. `feat/phase-7.0-example-rule`
2. `rules/birth_year_match.py`:

   ```python
   class BirthYearMatchRule:
       rule_id = "birth_year_match"

       def apply(self, subject_a, subject_b, context):
           a = subject_a.get("birth_year")
           b = subject_b.get("birth_year")
           if a is None or b is None:
               return []
           diff = abs(a - b)
           if diff == 0:
               return [Evidence(
                   rule_id=self.rule_id,
                   direction=EvidenceDirection.SUPPORTS,
                   weight=0.8,
                   observation=f"Birth year exact match ({a})",
               )]
           if diff <= 2:
               return [Evidence(
                   rule_id=self.rule_id,
                   direction=EvidenceDirection.SUPPORTS,
                   weight=0.4,
                   observation=f"Birth year within 2 years (Δ={diff})",
               )]
           if diff > 10:
               return [Evidence(
                   rule_id=self.rule_id,
                   direction=EvidenceDirection.CONTRADICTS,
                   weight=0.6,
                   observation=f"Birth year diverges significantly (Δ={diff})",
               )]
           return []
   ```

3. Интеграционный тест:

   ```python
   def test_compose_same_person_hypothesis_zhitnitzky():
       """Demo: subject_a = Vladimir 1945, subject_b = Volodya 1945
       → BirthYearMatchRule yields strong supporting evidence."""
       register_rule(BirthYearMatchRule())
       a = {"given": "Vladimir", "surname": "Zhitnitzky", "birth_year": 1945}
       b = {"given": "Volodya", "surname": "Жitницкий", "birth_year": 1945}
       hypothesis = compose_hypothesis(
           HypothesisType.SAME_PERSON, a, b, context={}
       )
       assert hypothesis.composite_score >= 0.5
       assert any(e.rule_id == "birth_year_match" for e in hypothesis.evidences)
   ```

4. `pwsh scripts/check.ps1` зелёное
5. Commit, push, PR

### Task 4 — docs: research note on hypothesis-aware genealogy (опционально)

**Цель:** короткая заметка о том, чем подход отличается от Ancestry/MyHeritage.

`docs/research/hypothesis-aware-genealogy.md`:

- Comparison table: AutoTreeGen vs Ancestry hints vs MyHeritage Smart Matches
- Why explainability matters in scientific genealogy
- Future directions: counter-evidence, alternative hypotheses, evidence
  graph visualization (Phase 7.x)

`pwsh scripts/check.ps1` зелёное. Commit, push, PR.

---

## Что НЕ делать

- ❌ ORM persistence (Hypothesis в БД) — Phase 7.2
- ❌ HTTP API (`/hypotheses/...`) — Phase 7.3
- ❌ Bayesian networks / probabilistic graphs — overkill MVP
- ❌ LLM-augmented rules — Phase 10
- ❌ Реальная интеграция с entity-resolution — Phase 7.x когда Phase 3.4 закроется
- ❌ Трогать пакеты других агентов
- ❌ `git commit --no-verify`
- ❌ Мердж с красным CI

---

## Сигналы успеха

После 3 PR (Task 4 опционально):

1. ✅ ADR-0016 в `docs/adr/`
2. ✅ `packages/inference-engine/` scaffolded, ≥80% test coverage
3. ✅ `BirthYearMatchRule` работает в integration test
4. ✅ Plugin protocol позволяет registering arbitrary rules
5. ✅ Все CI зелёные
6. ✅ Demo: Vladimir 1945 vs Volodya 1945 → composite ≥ 0.5

---

## Coordination

- Никаких пересечений на уровне файлов с Агентами 1-5
- Корневой `pyproject.toml` / `uv.lock` — если конфликт, rebase + uv lock
- ROADMAP §7+ секции свободные

---

## Что дальше (для контекста, не делать)

- **Phase 7.1** — больше rules (Daitch-Mokotoff surname, place match, sex
  match, parent-age sanity, DNA cM range matching)
- **Phase 7.2** — ORM persistence: Hypothesis + Evidence + alternatives
- **Phase 7.3** — HTTP API: `POST /hypotheses` (compute), `GET /hypotheses/{id}`
- **Phase 7.4** — UI: hypothesis graph visualization, manual evidence
  add/remove
- **Phase 10** — LLM-augmented rules для свободного текста (e.g. "найди
  упоминания Slonim в OBJE notes")

Удачи. Это **сердце AutoTreeGen** — где математически становится
доказуемо что AutoTreeGen уникален по сравнению с walled-garden кон
