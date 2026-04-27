# ADR-0016: Inference engine architecture (hypothesis-aware genealogy)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `inference`, `hypothesis`, `evidence-graph`, `phase-7`

## Контекст

CLAUDE.md секция 3 фиксирует один из нерушимых архитектурных принципов
проекта:

> **Hypothesis-aware.** Гипотезы — first-class entity, не «черновики».
> Хранятся с rationale и evidence-graph.

Это и есть то, что отличает AutoTreeGen от walled-garden конкурентов
(Ancestry, MyHeritage, FamilySearch). Они показывают «Hint: this is your
ancestor» — без объяснения *почему* алгоритм так считает, без
counter-evidence, без альтернативных гипотез. Пользователь видит
бинарное «accept / reject» и должен либо доверять чёрному ящику, либо
игнорировать его. Для **научной** генеалогии этого недостаточно: каждое
утверждение должно быть проверяемым, объяснимым и опровержимым.

Ровно такая же мысль в evidence-first принципе (CLAUDE.md §3.1) и
жёстком запрете на auto-merge близкого родства без manual review
(§5). Алгоритмы матчинга у нас уже появляются в нескольких местах:

- **Phase 3.4 (entity-resolution, ADR-0015)** — fuzzy-match персон,
  мест, источников. Возвращает suggestions со score и причиной, но
  внутри сидит ad-hoc weighted sum.
- **Phase 6.1 (dna-analysis, ADR-0014)** — half-IBD сегменты + Shared
  cM Project relationship prediction. Сейчас выдаёт ranked list,
  тоже без формализованного evidence-graph.
- **Phase 5.x (familysearch-client, ADR-0011)** — будут «возможно тот
  же человек в FamilySearch» suggestions.

Без общего фреймворка каждый из этих пакетов изобретает собственное
понятие «evidence», «score», «confidence» — c риском что в Phase 7.x,
когда мы захотим composability (DNA-evidence + name-evidence +
date-evidence для одной гипотезы), форматы не сойдутся.

Phase 7.0 — **скелет общего движка гипотез**, заполнение rule'ами идёт
в Phase 7.1+. Цель Phase 7.0 — зафиксировать словарь и контракты:
что такое Hypothesis, Evidence, InferenceRule, как они композируются
в HypothesisGraph. Persistence (ORM) и HTTP API — Phase 7.2 и 7.3
соответственно, не сейчас.

Силы давления на решение:

1. **Determinism.** CLAUDE.md §3.6 — «Deterministic > magic. LLM
   применяется только там, где он реально полезен (Phase 10)». Базовый
   движок гипотез — детерминированные функции от входов, никакого LLM
   в core engine. LLM-augmented rules — отдельная история Phase 10,
   они должны вписываться в тот же контракт InferenceRule.
2. **Pure functions без I/O.** Совместимо с архитектурой пакетов
   `dna-analysis` (ADR-0012) и `entity-resolution` (ADR-0015) — никаких
   ORM, БД, HTTP внутри inference-engine. Это позволяет тестировать
   движок на синтетике + переиспользовать в скриптах, CLI, batch jobs.
3. **Composability.** Один rule может использовать output другого:
   например, `SamePersonRule` может агрегировать evidence из
   `SameSurnameRule` + `BirthYearMatchRule` + `DnaSegmentMatchRule`.
   Архитектура должна это разрешать без циклов.
4. **Plugin-friendly.** Будущие пакеты (entity-resolution, dna-analysis,
   familysearch-client) должны регистрировать свои rules без
   модификации inference-engine — иначе пакет станет узким горлышком.
5. **Provenance everywhere (CLAUDE.md §3.3).** Каждый Evidence несёт
   pointer на свой источник: какой rule произвёл, на каких данных, с
   какой версии reference table. Это нужно для Phase 7.2+ persistence
   и для UI «почему мы так считаем».
6. **Counter-evidence first-class.** Hypothesis-aware ≠ просто
   «суммируем плюсы». `direction=CONTRADICTS` — равноправный гражданин,
   потому что для опровержения гипотезы (X не отец Y, потому что
   родился позже) нужно отличать «нет данных» от «есть данные против».

## Рассмотренные варианты

### Вариант A — Pure-function rules + composition (выбран)

Каждый InferenceRule — pure function:

```text
(subject_a, subject_b, context) -> list[Evidence]
```

Rule's зарегистрированы в **explicit registry** (модуль `rules/registry.py`)
или подгружены через entry point в Phase 7.1+. Composer применяет все
зарегистрированные (или явно переданный subset) rules к паре сущностей,
собирает Evidence в Hypothesis, считает composite score.

Composite score (MVP формула):

```text
score = clamp(
    Σ(weight_i for SUPPORTS) - Σ(weight_j for CONTRADICTS),
    0, 1
)
```

Это **не Bayes posterior** — для строгой Bayes-оценки нужен prior, а в
Phase 7.0 у нас нет генеалогического контекста дерева. Bayes — Phase 7.4
или позже, когда появится tree-context prior (см. ADR-0014 §«predict
relationship» — там та же мотивация).

Композиция rule's:

- **Plain composition:** Composer вызывает каждый rule, склеивает
  Evidence-list. Это делается в `compose_hypothesis()`.
- **Rule-chained composition (Phase 7.1+):** rule может принимать
  output другого rule через context (`context["dna_segments"]` для
  DnaSegmentEvidenceRule). В Phase 7.0 контракт фиксируется, but
  фактического chaining ещё нет — он появится с реальными rule's.

Плюсы:

- ✅ Полностью детерминированный, тестируемый на синтетике.
- ✅ Pydantic-models дают валидацию типов и сериализацию из коробки —
  в Phase 7.2 ORM-модели будут maps к Pydantic 1:1.
- ✅ Нет внешних зависимостей кроме Pydantic — пакет можно использовать
  в любом контексте (CLI, service, notebook).
- ✅ Plugin protocol через `runtime_checkable` Protocol — registering
  rules не требует базового класса, любой объект с `apply()` подходит.
- ✅ Линейная композиция понятна: пользователь видит вклад каждого rule
  в финальный score (это и есть «evidence-graph»).
- ✅ Совместимо с ADR-0015 (entity-resolution): когда Phase 7.x
  интеграция с entity-resolution случится, ER-features будут
  обёрнуты в Evidence без изменения контракта.

Минусы:

- ❌ Линейная weighted-sum формула не решает калибровку confidence.
  Пользователь увидит score=0.92, но это не вероятность. Mitigation:
  в UI Phase 7.4+ score → bucket «highly likely / likely / possible /
  unlikely» с явной calibration table (ROADMAP §7.4).
- ❌ Корреляция между evidences не учитывается (если два rule's
  смотрят на одни и те же данные с разных углов, weights суммируются
  как независимые). Mitigation: документируется как known limitation;
  Phase 7.5 — Bayes-network если станет проблемой.
- ❌ Plain dict-based subject передача (а не строгая Pydantic-модель
  PersonForMatching). Это compromise: разные пакеты делают свои
  представления Person/Place/Source, и принуждение к одному типу
  создаёт hard coupling. В Phase 7.0 контракт — dict, в Phase 7.2+
  можно ввести SubjectProtocol при необходимости.

### Вариант B — Bayesian network library (pgmpy / pomegranate)

Использовать готовую библиотеку для Bayes networks: hypothesis = node,
evidence = observed variable, conditional probabilities прописаны вручную.

- ✅ Math-grounded: настоящие posterior probabilities, не weighted sum.
- ✅ Counter-evidence естественно через CPT.
- ❌ Overkill для MVP — мы не имеем prior probabilities для большинства
  rule's. «Вероятность same-person при surname match» — это не цифра,
  это субъективная оценка, которая зависит от частоты фамилии в популяции.
- ❌ Bayes-network библиотеки тянут numpy/scipy/networkx в core пакет,
  что нарушает «лёгкий core» политику.
- ❌ Не детерминированно для inference на больших networks (sampling-based
  алгоритмы); для unit-тестов — головная боль с seed'ом.
- ❌ Невозможно объяснить пользователю «почему» прозрачным образом —
  CPT'шки требуют знания теории.
- Когда пересмотреть: появится ≥10 rule's и реальные prior'ы из tree-context.

### Вариант C — Rule engine (Drools / clips / experta)

Forward-chaining production rules: «if A and B then conclude C».

- ✅ Эспертные системы хорошо ложатся на genealogy: правила легко читать.
- ✅ Прозрачное объяснение через trace применённых правил.
- ❌ Production rules дают булево заключение, а нам нужны continuous
  scores. Adapting производит sub-optimal result.
- ❌ `experta` (Python clips) заброшен с 2020 — production-risk.
- ❌ Drools — Java, не Python-native. Не вписывается в стек.
- Когда пересмотреть: если появится requirement от пользователей на
  declarative rule editing через UI без code change.

### Вариант D — LLM-driven inference (Phase 10, не сейчас)

Передавать пары сущностей в LLM, просить ranked hypotheses + reasoning.

- ✅ Гибкость: LLM понимает свободный текст, контекст, нюансы (e.g.
  «Volodya — это диминутив от Vladimir»).
- ❌ Не детерминированно — два прогона дают разный score.
- ❌ Нет provenance в строгом смысле — «LLM так сказал».
- ❌ Дорого на больших деревьях (10k персон × N rule's).
- ❌ LLM это `Phase 10` (CLAUDE.md §3.6 явно об этом).

LLM-augmented rules в Phase 10 — отдельный rule, который вписывается в
тот же контракт `InferenceRule.apply()`. Они **дополняют**
детерминированные rule's, не заменяют их. Архитектура (Вариант A)
это поддерживает без изменений.

## Решение

Принят **Вариант A — pure-function rules + composition.**

### Core types

| Тип | Назначение | Persistence (Phase 7.2) |
|---|---|---|
| `Hypothesis` | Claim о связи между двумя entities (same-person, parent-child, marriage, sibling). Содержит evidences + composite_score + alternatives. | Таблица `hypotheses` |
| `Evidence` | Атомарный факт supporting / contradicting / neutral для гипотезы. Несёт rule_id, weight, observation, source_provenance. | Таблица `hypothesis_evidences` (FK на hypothesis_id) |
| `InferenceRule` | Pure function (subject_a, subject_b, context) → list[Evidence]. Идентифицируется `rule_id`. | Не персистится — code-defined |
| `HypothesisType` | Enum: SAME_PERSON, PARENT_CHILD, SIBLINGS, MARRIAGE. | Enum в hypotheses.type |
| `EvidenceDirection` | Enum: SUPPORTS, CONTRADICTS, NEUTRAL. | Enum в evidences.direction |

### Plugin protocol

```python
@runtime_checkable
class InferenceRule(Protocol):
    rule_id: str

    def apply(
        self,
        subject_a: dict,
        subject_b: dict,
        context: dict,
    ) -> list[Evidence]: ...
```

Любой объект с этими атрибутами регистрируется через `register_rule()`.
В Phase 7.0 — explicit registry; в Phase 7.1+ может появиться
auto-discovery через `pyproject.toml` entry points.

### Composer

```python
def compose_hypothesis(
    hypothesis_type: HypothesisType,
    subject_a: dict,
    subject_b: dict,
    context: dict,
    rules: list[InferenceRule] | None = None,  # default: all registered
) -> Hypothesis:
    ...
```

Алгоритм:

1. Если `rules is None` → берём всё из registry.
2. Для каждого rule вызываем `rule.apply(subject_a, subject_b, context)`.
3. Склеиваем list[Evidence] всех rule's.
4. Считаем `composite_score` по формуле выше.
5. Возвращаем Hypothesis (alternatives — пустой list в Phase 7.0;
   alternative-generation — Phase 7.4).

### Что фиксируется здесь как контракт

- **InferenceRule — pure function.** Никакого I/O (БД, HTTP, файлы),
  никаких side-effects (логирование агрегатов — ok, см. ADR-0012).
- **Evidence несёт provenance.** Минимум: rule_id, observation. В
  Phase 7.1+ source_provenance расширится `{"reference_data": "Shared
  cM Project 4.0", "version": "..."}`.
- **Counter-evidence first-class.** `direction=CONTRADICTS` снижает
  score, но Evidence остаётся видимым в hypothesis для UI explanation.
  «Нет данных» (NEUTRAL) — отдельный класс, не путаем с «нет evidence».
- **Score range [0, 1].** Composite формула clamps в этот диапазон.
  В UI 0.0—1.0 переводится в человеческие bucket'ы (Phase 7.4).
- **Composability ≠ rule chaining.** В Phase 7.0 rule's применяются
  независимо к одной паре. Chaining через context — Phase 7.1+ (когда
  появятся реальные rule's типа DnaSegmentMatchRule, которые потребляют
  output из dna-analysis).

### Что НЕ фиксируется здесь (отложено)

- ORM persistence — Phase 7.2 (отдельный ADR-0017 или эволюция этого).
- HTTP API — Phase 7.3.
- Calibration table «score → human bucket» — Phase 7.4 + UI.
- Bayesian / probabilistic networks — Phase 7.5+ если weighted sum
  окажется недостаточно.
- LLM-augmented rules — Phase 10 (вписываются в InferenceRule
  Protocol без изменений).
- Реальная интеграция с entity-resolution / dna-analysis /
  familysearch-client — соответствующие Phase 7.x.

## Последствия

**Положительные:**

- Phase 7.0 даёт общий словарь для всех будущих rule-based фич.
  Phase 3.4, 6.1, 5.x, 6.4 используют один и тот же Hypothesis +
  Evidence формат — composability работает с момента, когда rule's
  начнут регистрироваться.
- Pure-functions архитектура совместима с другими пакетами
  (`dna-analysis`, `entity-resolution`, `familysearch-client`) — все
  они уже без I/O в core. Inference-engine не вносит новой парадигмы.
- Тестирование на синтетике: одна пара сущностей + один rule = один
  Evidence + assertion на score. Phase 7.0 поставит ≥80% покрытия
  на скелете и BirthYearMatchRule.
- UI Phase 7.4 получит готовый JSON: hypothesis с evidence-list, где
  каждый Evidence уже содержит human-readable observation. Никакой
  пост-обработки в UI не требуется.
- Provenance прорастает: каждый Evidence знает свой rule_id и source,
  evidence-graph можно сериализовать целиком в JSON для аудита.

**Отрицательные / стоимость:**

- Pydantic-models надо поддерживать в sync с Phase 7.2 ORM.
  Mitigation: model_dump / model_validate уже даёт нам конверсию;
  ORM-схема будет 1:1 с Pydantic-полями.
- Композиция через linear weighted sum — не Bayes. Если в реальных
  деревьях окажется, что score плохо коррелирует с верностью гипотезы,
  это придётся пересматривать (см. «Когда пересмотреть»).
- Для каждого нового rule нужен явный test suite — без формальной Bayes
  модели выбор weights субъективен. В Phase 7.1+ — обязательно
  калибровка на реальных primary-source данных, не только синтетика.

**Риски:**

- **Wrong abstraction risk.** Если через 2-3 rule's окажется, что
  Hypothesis / Evidence / InferenceRule не покрывают реальные кейсы
  (например, multi-subject hypothesis типа «эти три персоны — одно
  семейство»), придётся менять core types. Mitigation: Phase 7.0 —
  скелет, а не финальный API; контракт версионируется (`Hypothesis.v1`).
- **Score inflation.** Несколько rule's, смотрящих на одни и те же
  данные (e.g. surname-match + initial-match), накапливают weights без
  учёта корреляции — score завышается. Mitigation: документируем,
  Phase 7.5 — Bayes / explicit correlation matrix.
- **Plugin loading в production.** Auto-discovery через entry points
  не реализуется в Phase 7.0 — explicit `register_rule()` calls в
  service init code. Если service забывает зарегистрировать rule,
  hypothesis возвращается с пустым evidences. Mitigation: Phase 7.3
  HTTP service на старте логирует список registered rule's.
- **Counter-evidence misuse.** Rule может «защищать» гипотезу,
  возвращая SUPPORTS даже на слабом match'е. Mitigation: каждый rule
  обязан в docstring явно прописать пороги для SUPPORTS / CONTRADICTS /
  NEUTRAL, и code-review проверяет калибровку.

**Что нужно сделать в коде (Phase 7.0):**

1. `packages/inference-engine/pyproject.toml` — uv workspace member,
   зависимости: только `pydantic>=2.10`. Добавить в корневой
   `pyproject.toml` workspace + sources.
2. `src/inference_engine/types.py` — Pydantic models Hypothesis,
   Evidence + Enum HypothesisType, EvidenceDirection.
3. `src/inference_engine/rules/base.py` — InferenceRule Protocol.
4. `src/inference_engine/rules/registry.py` — explicit registry
   (module-level dict + register_rule / get_rule / all_rules).
5. `src/inference_engine/composer.py` — compose_hypothesis function +
   weighted-sum scorer.
6. `src/inference_engine/rules/birth_year_match.py` — first concrete
   rule (Phase 7.0 example). Phase 7.1 добавит остальные rules.
7. Тесты ≥80% coverage: types validation, registry CRUD, composer
   formula, BirthYearMatchRule edge cases, integration test
   (Vladimir 1945 vs Volodya 1945 → composite ≥ 0.5).
8. README с примерами использования.
9. ROADMAP §7+ — отметить Phase 7.0 как done после merge.

## Когда пересмотреть

- **>10 rule's зарегистрировано** → проверить калибровку весов на
  реальных данных, рассмотреть переход на Bayes (Вариант B) если
  weighted sum даёт misleading scores.
- **Появляется prior из tree-context** (Phase 7.4 + tree-graph) →
  composer переходит на posterior-style формулу с tree-prior,
  но контракт InferenceRule остаётся.
- **Multi-subject hypotheses** (gen-3 family identification: дед+бабка+отец
  одна семья из трёх персон) → Hypothesis расширится до `subjects: list`,
  это будет ADR-0017 с migration path.
- **Performance: composition >100 ms на пару** → переход на batch-API
  `compose_hypotheses_batch(pairs, rules)` с векторизацией.
- **LLM-augmented rules входят в use** (Phase 10) → проверить, что
  Protocol выдерживает stochastic output (rule может вернуть list с
  разной weight на разных runs); вероятно, добавим `seed` в context.
- **Runtime rule editing** через UI / DSL → перейти к external rule
  storage (Drools-style, вариант C), но это большой redesign — Phase
  10+.

## Ссылки

- Связанные ADR:
  - ADR-0014 (DNA matching algorithm) — `predict_relationship` уже
    возвращает ranked list; в Phase 7.x будет обёрнут в InferenceRule.
  - ADR-0015 (entity-resolution suggestions) — fuzzy-match scores
    станут Evidence от ER-rules.
  - ADR-0011 (familysearch-client design) — будущие FS suggestion's
    тоже через InferenceRule.
  - ADR-0012 (DNA privacy & architecture) — pure-functions подход,
    которым inference-engine следует.
- CLAUDE.md §3.1 (evidence-first), §3.2 (hypothesis-aware), §3.6
  (deterministic > magic).
- ROADMAP §7 (Phase 7 — Inference engine).
- Внешние:
  - [Bayesian network libraries (pgmpy)](https://pgmpy.org/) — Вариант B reference.
  - [Experta — Python expert system](https://github.com/nilp0inter/experta) — Вариант C reference.
  - [Genealogical Proof Standard](https://bcgcertification.org/ethics-standards/) — методологический контекст для evidence-based генеалогии.
