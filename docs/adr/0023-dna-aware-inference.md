# ADR-0023: DNA-aware inference rule

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `inference`, `dna`, `evidence-graph`, `endogamy`, `phase-7`

## Контекст

После Phase 7.0 / 7.1 у нас работает hypothesis-aware inference engine
(ADR-0016): rules — pure-функции `(subject_a, subject_b, context) ->
list[Evidence]`, composer склеивает их в `Hypothesis` с composite score.
Сейчас в наборе четыре GEDCOM-rules: `SurnameMatchRule`,
`BirthYearMatchRule`, `BirthPlaceMatchRule`, `SexConsistencyRule`. Все
четыре оперируют именами / датами / местами — то есть классическим
бумажным evidence.

Phase 6.0 / 6.1 уже даёт DNA pipeline: парсеры 23andMe / AncestryDNA,
half-IBD shared-segment finder, relationship prediction по Shared cM
Project 4.0 (ADR-0014). Phase 6.2 закрывает DNA-service — encrypted
storage, consent flow, pairwise matching endpoint. То есть **DNA-данные
уже структурированы и доступны**, но inference-engine их не видит.

Это и есть самый дорогой gap. ThruLines у Ancestry популярны не потому,
что они угадывают родство по именам, а потому что DNA подтверждает или
отбрасывает гипотезу. Без DNA-rule inference-engine остаётся
GEDCOM-only — то есть на уровне entity-resolution, без главного
дифференциатора, ради которого собрана инфраструктура из
Phase 6.0 / 6.1 / 6.2.

Phase 7.3 закрывает первый killer-rule: **DnaSegmentRelationshipRule** —
pairwise rule, который потребляет DNA-aggregate и выдаёт SUPPORTS /
CONTRADICTS для гипотез `SAME_PERSON` / `PARENT_CHILD` / `SIBLINGS` на
основе total shared cM, с поправкой на endogamy.

Силы давления на решение:

1. **Pure-functions контракт inference-engine (ADR-0016).** Никакого I/O
   внутри rule'а — ни ORM, ни HTTP. Всё, что нужно для решения,
   приходит через `subject_a`, `subject_b`, `context`. Значит, DNA
   evidence должен быть **pre-loaded** caller'ом и передан в context.
2. **Domain-aware (CLAUDE.md §3.7).** Целевая популяция — Ashkenazi
   Jewish + восточно-европейская диаспора, обе endogamous. Без
   endogamy-коррекции standard Shared cM таблица даёт false-high
   confidence на distant matches: 50 cM в AJ-cohort часто означает
   5–7 cousin'а, а не 3–4 (Bettinger studies). Rule **обязан** уметь
   снижать confidence когда хотя бы один subject из endogamous группы.
3. **Pairwise hypothesis only (Phase 7.x model).** `HypothesisType` —
   pairwise (SAME_PERSON / PARENT_CHILD / SIBLINGS / MARRIAGE).
   Multi-subject (cluster of N descendants → single common ancestor) —
   Phase 7.4 + extension `subjects: list`. Cluster-rule из брифа
   Phase 7.3 в этот ADR **не входит**.
4. **Privacy by design (ADR-0012, ADR-0020).** Никаких raw rsids /
   genotypes / SNP-calls в `Evidence.observation` или
   `source_provenance`. Только агрегаты: total_cm, longest_segment_cm,
   segment_count, kit-id (псевдоним), ethnicity_population enum.
5. **Reproducibility.** `rules_version` (Phase 7.2 hypothesis_runner)
   должен изменяться при добавлении DNA-rule. Это и так автоматически —
   `_DEFAULT_RULE_CLASSES` пересчитывает sha8 при добавлении класса
   (см. `services/parser-service/.../hypothesis_runner.py`).

## Рассмотренные варианты

### Вариант A — Pairwise pure rule, DNA aggregate в context (выбран)

`DnaSegmentRelationshipRule.apply(subject_a, subject_b, context)`:

- `subject_a`, `subject_b` — обычные person-dict'ы (`id`, `given`,
  `surname`, `birth_year`, …) тот же формат что hypothesis_runner уже
  собирает в `_person_to_subject`.
- `context["dna_evidence"]` — опциональный dict-aggregate; если
  отсутствует, rule возвращает пустой list.
- `context["hypothesis_type"]` — `"same_person"` / `"parent_child"` /
  `"siblings"`. Для `"marriage"` rule silent (см. §«Что НЕ делать»).

DNA-aggregate shape (фиксируется этим ADR):

```python
context["dna_evidence"] = {
    "total_cm": float,                  # сумма cM всех shared segments
    "longest_segment_cm": float,        # самый длинный сегмент
    "segment_count": int,                # количество сегментов
    "ethnicity_population_a": str,       # EthnicityPopulation enum value
    "ethnicity_population_b": str,
    "source": str,                       # "ancestry_match_list" |
                                         # "computed_pairwise" | ...
    "kit_id_a": str | None,              # псевдоним кита (UUID-строка)
    "kit_id_b": str | None,
}
```

Все поля aggregate-only; raw rsid/genotype не попадает в context.
Caller'у (hypothesis_runner Phase 7.3.1) предстоит собрать этот dict
из `DnaKit` + `DnaMatch` ORM, что **за пределами scope** этого ADR.

Плюсы:

- ✅ Полностью pairwise — вписывается в текущий `HypothesisType`
  без расширения модели.
- ✅ Вписывается в pure-functions контракт ADR-0016 — никакого I/O.
- ✅ Тестируется на синтетике без БД (передаём context-dict вручную).
- ✅ Composability: rule живёт рядом с GEDCOM-rules, composer уже
  складывает evidence от всех rules в один `Hypothesis`.
- ✅ Endogamy-коррекция явная и аудитируемая — multiplier попадает
  в `Evidence.source_provenance` для evidence-graph UI.

Минусы:

- ❌ Caller обязан pre-load'ить DNA-aggregate. В Phase 7.3 caller'а ещё
  нет — только unit-тесты. Phase 7.3.1 расширит hypothesis_runner.
- ❌ Per-segment data (chr, start_bp, end_bp) на уровне `DnaMatch` ORM
  не хранится — там только агрегаты. Это known limitation: Phase 7.4+
  введёт `DnaSegment` table, тогда Evidence сможет включать конкретные
  сегменты в provenance. Сейчас rule оперирует aggregate-only.

### Вариант B — Multi-subject cluster rule (отложен → Phase 7.4)

`DnaCommonAncestorClusterRule`: ≥ 3 kit'а share один сегмент → cluster
с predicted common ancestor.

- ✅ Очень полезный сигнал на эмпирически отдалённых matches: один
  segment, повторяющийся у трёх descendant'ов разных known персон,
  устойчив к endogamy лучше pairwise total cM.
- ❌ `HypothesisType` сейчас pairwise (`subject_a_id` / `subject_b_id` —
  UUID, не list). Multi-subject требует расширения core types: либо
  `subjects: list[UUID]`, либо специальный `ClusterHypothesis` подтип.
  Это **breaking change** для inference-engine API → нужен отдельный
  ADR (Phase 7.4).
- ❌ Per-segment data — но `DnaMatch` ORM хранит только агрегаты
  (total_cm, largest_segment_cm). Per-segment store — отдельная
  миграция (`DnaSegment` table) и отдельный ADR.
- ❌ MVP-cluster-rule на больших cohort'ах O(n²) комбинаций → нужно
  предварительное сегментное группирование, тоже не входит сюда.

Решение: cluster-rule переезжает в Phase 7.4 ADR-0024 (или соседний
номер) с явным extension'ом core types.

### Вариант C — DNA-rule в отдельном пакете (`dna-inference`)

Создать `packages/dna-inference/` с собственным rule + helper'ами.

- ✅ Изолирует DNA-логику от GEDCOM-rules.
- ❌ `inference-engine.rules.*` — естественное место для plug-in'ов.
  GEDCOM-rules уже лежат там, и они полагаются на `entity-resolution`
  через обычный import. Та же модель работает для DNA: rule живёт в
  `inference_engine.rules.dna`, при необходимости импортит helpers
  из `dna-analysis`.
- ❌ Дополнительный пакет = дополнительный pyproject + workspace +
  CI matrix entry. Излишне для одного rule'а в Phase 7.3.

Решение: rule живёт внутри `inference-engine` в файле `rules/dna.py`,
тестируется тем же conftest.py что остальные rules.

## Решение

Принят **Вариант A — pairwise rule в `inference-engine.rules.dna`**.

### Контракт DnaSegmentRelationshipRule

```python
class DnaSegmentRelationshipRule:
    rule_id = "dna_segment_relationship"

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]: ...
```

Subject-контракт: ключи произвольные (rule сам по себе не читает поля
subject'ов — DNA-данные приходят через context). Поле `id` присутствует
в subject'ах, заполняемых hypothesis_runner — для UI provenance.

Context-контракт:

- `context["hypothesis_type"]` — обязателен. Допустимые значения:
  `"same_person"`, `"parent_child"`, `"siblings"`. Для остальных
  (`"marriage"`, любые DUPLICATE_*) rule silent (пустой list).
- `context["dna_evidence"]` — опционален. Shape — см. §«Вариант A».
  Если отсутствует или пуст — rule silent.

### cM-пороги (Shared cM Project 4.0, CC-BY 4.0)

Источник — та же таблица, что в
`packages/dna-analysis/src/dna_analysis/matching/relationships.py`
(ADR-0014). Этот ADR не дублирует таблицу: rule инлайнит точечные
пороги, релевантные для конкретных hypothesis types, с явной ссылкой
на source.

| Hypothesis | SUPPORTS (cM-диапазон) | CONTRADICTS (cM-диапазон) |
|---|---|---|
| `same_person` | ≥ 3 400 cM (identical twin / self) | < 1 500 cM (≪ full sibling lower bound) |
| `parent_child` | 2 376 – 3 720 cM | < 1 500 cM или > 3 800 cM (twin/same зона) |
| `siblings` | 1 613 – 3 488 cM (full sibling range) | < 1 000 cM (≪ full sibling lower bound) |

Дальние родства (great-grandparent / 1C / etc.) **не покрываются** этим
rule в Phase 7.3 — там cM-диапазоны overlapping, и без cluster-сигнала
pairwise total cM даёт слабый дискриминационный сигнал. Это known
limitation; Phase 7.4 cluster-rule покроет distant relationships
устойчивым способом.

Шум-floor: **`total_cm < 7 cM`** → rule silent (synchronized с
`packages/dna-analysis/.../relationships.py`, ADR-0014).

### Confidence formula (weights)

| Случай | direction | weight |
|---|---|---|
| `same_person` SUPPORTS (≥ 3 400 cM) | SUPPORTS | 0.85 |
| `same_person` CONTRADICTS (< 1 500 cM) | CONTRADICTS | 0.85 |
| `parent_child` SUPPORTS (in range) | SUPPORTS | 0.80 |
| `parent_child` CONTRADICTS | CONTRADICTS | 0.70 |
| `siblings` SUPPORTS | SUPPORTS | 0.65 |
| `siblings` CONTRADICTS | CONTRADICTS | 0.60 |

Веса намеренно высокие для close-relationship hypotheses: DNA — самый
сильный available evidence, и weighted-sum composer (ADR-0016) должен
позволять одной DNA-evidence перевешивать совокупность name+date+place
сигналов.

### Endogamy adjustment

`EthnicityPopulation` enum (`shared_models.enums`) уже знает
multiplier'ы из Bettinger studies:

- `general` → 1.0
- `ashkenazi` → 1.6
- `sephardi` → 1.4
- `amish` → 2.0
- `lds_pioneer` → 1.5

Применение:

1. Эффективный multiplier для пары = max(`mul_a`, `mul_b`) — самый
   консервативный (если хоть один subject endogamous, корректируем).
2. **Не переклассифицируем direction.** Меняем только weight:
   `effective_weight = base_weight / multiplier`. Т.е. AJ-кейс с
   multiplier = 1.6 даёт SUPPORTS weight 0.85 / 1.6 ≈ 0.53 вместо 0.85.
3. Multiplier попадает в `Evidence.source_provenance` —
   evidence-graph UI покажет «AJ adjustment ÷1.6 applied».

Этот подход проще, чем `effective_cm = total_cm / multiplier` с
переклассификацией: он сохраняет user-facing observation честным
(«общий cM = X»), а уверенность снижает явно.

### Privacy guards

- Rule НЕ имеет доступа к raw rsid / genotype / SNP-calls. Только
  pre-aggregated context-dict (см. shape выше).
- `Evidence.observation` — человеко-читаемая строка с total_cm и
  hypothesis-status. Без kit_id (UUID-псевдоним пишется в
  `source_provenance`, не в observation — ADR-0012).
- `Evidence.source_provenance` содержит: rule_id, total_cm,
  longest_segment_cm, segment_count, endogamy multiplier, source
  attribution. Никаких genotypes.
- Logs (`_LOG.debug`): только агрегаты, синхронизированно с правилами
  ADR-0012 / ADR-0014.

### Что фиксируется здесь

- Pairwise DnaSegmentRelationshipRule с приведённым выше API.
- cM-пороги для SAME_PERSON / PARENT_CHILD / SIBLINGS из Shared cM
  Project 4.0.
- Endogamy adjustment как weight-divisor через
  `EthnicityPopulation` enum (без переклассификации direction).
- Privacy: aggregate-only context, никаких raw genotypes.
- Шум-floor 7 cM = тот же что ADR-0014.

### Что НЕ фиксируется здесь (отложено)

- **Cluster rule (multi-subject).** → Phase 7.4 ADR + extension
  `HypothesisType` к `subjects: list`.
- **Per-segment storage (`DnaSegment` table).** → Phase 7.4 миграция +
  отдельный ADR. Сейчас rule оперирует только aggregate `total_cm` /
  `longest_segment_cm` / `segment_count` из существующего `DnaMatch`.
- **hypothesis_runner integration.** Регистрация
  `DnaSegmentRelationshipRule` в `_DEFAULT_RULE_CLASSES` и расширение
  `_person_to_subject` (либо новый context-loader для DNA-aggregate) —
  Phase 7.3.1, отдельный PR. Этот ADR описывает контракт; runner-side
  implementation идёт следующим.
- **Auto-detection endogamy.** Сейчас `EthnicityPopulation` ставится
  вручную (через UI / API). Auto-detection по surname / cohort statistics —
  Phase 7.x, отдельная задача.
- **DnaContradictsRelationshipRule (брифом «Rule 3»).** Если в дереве
  записан parent_child но DNA даёт total_cm = 50 cM — warning. По
  логике это симметрично DnaSegmentRelationshipRule с CONTRADICTS-веткой,
  и фактически уже покрывается этим rule (см. таблицу CONTRADICTS).
  Отдельный rule не вводим, чтобы не дублировать сигнал — composer
  получит CONTRADICTS evidence от того же rule, и compositе score
  упадёт. UI Phase 4.6 показывает evidences по rule_id —
  пользователь увидит причину.
- **MARRIAGE hypothesis с DNA.** Endogamic группы массово женятся на
  distant cousins, и положительная total_cm не противоречит marriage.
  Игнорируем эту hypothesis в DNA-rule.

## Последствия

**Положительные:**

- Phase 7.3 закрывает главный gap inference-engine: DNA-rule
  становится частью composite score, evidence-graph UI начинает
  показывать DNA-evidence рядом с GEDCOM-evidence.
- Rule покрывает три самые ценные hypothesis types для пользователя:
  same_person (auto-merge candidate), parent_child (родительские
  цепочки), siblings (восстановление братьев-сестёр).
- Endogamy-коррекция работает с момента релиза — не false-high
  confidence на AJ-кейсах.
- Composability сохраняется: composer применяет DNA-rule поверх
  существующих GEDCOM-rules, никакой переписи Phase 7.1 кода.
- Provenance прозрачно: `Evidence.source_provenance` несёт total_cm,
  multiplier, source attribution → UI Phase 7.4 объяснит «почему».

**Отрицательные / стоимость:**

- В Phase 7.3 sit без runner-интеграции — rule не появится
  автоматически в production hypothesis-runs до Phase 7.3.1.
- Distant relationships (1C / 2C / great-grandparent) не покрыты —
  явный pull в сторону Phase 7.4 cluster-rule.
- Endogamy multiplier — единственный adjustment factor (population-level).
  Per-cohort calibration (например, AJ-Lithuanian vs AJ-Polish)
  отложена до Phase 8+ когда появятся реальные user-данные.

**Риски:**

- **Wrong multipliers.** Bettinger studies — единственный публичный
  numerical reference, и его значения 1.4–2.0 — оценки, не cohort-specific.
  Mitigation: документируем как baseline, в `EthnicityPopulation`
  enum значения уже зафиксированы; Phase 8+ — real-data calibration.
- **Cross-platform false-low cM.** 23andMe ↔ Ancestry overlap ~50–70%
  → observed total_cm может быть meaningfully ниже истинного.
  Rule этого не компенсирует. Mitigation: Phase 6.5 imputation +
  warning в `source_provenance` (caller — hypothesis_runner — может
  пометить cross-platform pair).
- **Score inflation от высоких weights.** DnaSegmentRelationshipRule с
  weight 0.85 на SUPPORTS перевешивает GEDCOM-rules. Это by design
  (DNA — самый сильный сигнал), но если caller случайно передаёт
  noise-DNA (например, < 7 cM) → rule silent, инфляции нет.

## Когда пересмотреть

- **Реальные user-данные показывают, что cM-пороги мисс-калибрированы**
  (false-positive same_person или false-negative parent_child) →
  пересборка таблицы порогов с явной calibration на known relationship
  pairs.
- **Cluster rule готова в Phase 7.4** → этот ADR ссылается на 7.4
  ADR; pairwise rule остаётся, но cluster дополняет его на distant
  relationships.
- **`DnaSegment` table введена** (Phase 7.4) → DnaSegmentRelationshipRule
  получает доступ к per-segment data в `source_provenance`, может
  reasoning'ать по конкретным сегментам (chromosome painter UI).
- **Auto-detection endogamy** становится reliable → context может
  включать computed multiplier вместо enum-based; rule API не меняется.
- **HypothesisType расширяется до multi-subject** → этот rule
  остаётся pairwise, multi-subject версия — отдельный класс
  `DnaClusterCommonAncestorRule` (Phase 7.4).
- **LLM-augmented DNA-rule** (Phase 10) — например, LLM анализирует
  family-tree topology + DNA aggregate и оценивает альтернативные
  родственные intervals. Вписывается в `InferenceRule` Protocol без
  изменений (см. ADR-0016 §«LLM-augmented rules»).

## Ссылки

- Связанные ADR:
  - ADR-0016 (inference engine architecture) — pure-functions контракт,
    composer формула, plugin protocol.
  - ADR-0014 (DNA matching algorithm) — Shared cM Project 4.0 source,
    cM-таблица, шум-floor 7 cM.
  - ADR-0012 (DNA privacy & architecture) — privacy guards для
    DNA-aggregate в context.
  - ADR-0020 (DNA service architecture) — `DnaTestRecord` /
    `DnaConsent` flow, на котором строится DNA-side caller.
  - ADR-0021 (hypothesis persistence) — `Hypothesis` /
    `HypothesisEvidence` ORM, в которые попадает DNA-evidence через
    composer + hypothesis_runner.
- Файлы кода:
  - `packages/inference-engine/src/inference_engine/rules/dna.py` —
    реализация (Phase 7.3).
  - `packages/inference-engine/tests/test_rules_dna.py` — unit + composer
    integration tests.
  - `packages/dna-analysis/src/dna_analysis/matching/relationships.py` —
    Shared cM Project 4.0 таблица-источник cM-порогов (ADR-0014).
  - `packages/shared-models/src/shared_models/enums.py:EthnicityPopulation`
    — endogamy multiplier enum.
  - `packages/shared-models/src/shared_models/orm/dna_kit.py` —
    `DnaKit.person_id` / `DnaKit.ethnicity_population`, отсюда runner
    Phase 7.3.1 заберёт данные для context.
- CLAUDE.md §3.1 (evidence-first), §3.2 (hypothesis-aware), §3.5
  (privacy by design), §3.7 (domain-aware: AJ / endogamy).
- ROADMAP §7 (Phase 7 — Inference engine).
- Внешние:
  - [Shared cM Project 4.0 — DNA Painter](https://dnapainter.com/tools/sharedcmv4) (CC-BY 4.0)
  - [Bettinger blog (Shared cM Project)](https://thegeneticgenealogist.com/)
  - [Endogamy and DNA — Genetic Genealogist](https://thegeneticgenealogist.com/2017/08/26/endogamy-and-dna/)
