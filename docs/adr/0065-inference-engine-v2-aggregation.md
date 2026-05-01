# ADR-0065: Inference engine v2 — confidence aggregation (Bayesian fusion + contradictions)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `inference-engine`, `phase-7`, `algorithm`, `migration`

> **Note:** изначально опубликован как ADR-0057, перенумерован 2026-05-01
> для разрешения коллизии трёх параллельных ADR-0057 (см. также
> ADR-0057 ai-hypothesis-explanation, ADR-0066 mobile-responsive-design-system).
> Внутренние ссылки в коде/доках обновлены на ADR-0065. Внешние upstream-ссылки
> на старое имя файла (`0057-inference-engine-v2-aggregation.md`) станут битыми —
> приемлемо, ADR ещё не публиковался вне репо.

## Контекст

Phase 7.0–7.4 использовали линейную weighted-sum формулу для
агрегирования evidence в composite_score (см. ADR-0016 §«Composer»):

```python
score = clamp(Σ supports.weight − Σ contradicts.weight, 0, 1)
```

В коде (`packages/inference-engine/src/inference_engine/composer.py`,
до Phase 7.5):

```python
def _composite_score(evidences):
    supports_total = sum(ev.weight for ev in evidences if ev.is_supports)
    contradicts_total = sum(ev.weight for ev in evidences if ev.is_contradicts)
    raw = supports_total - contradicts_total
    return max(0.0, min(1.0, raw))
```

Это давало три систематических проблемы, которые накопились к концу
Phase 7.4:

### 1. Корреляция между source'ами игнорируется

ADR-0016 §«Минусы» уже флагнул это как known limitation. Когда
несколько rule'ов смотрят на один и тот же сигнал с разных углов
(например, `surname_dm_match` выпускает два evidence из-за
multi-bucket overlap), оба добавляют свой weight, и composite
насыщается до 1.0 на «двух surname-доказательствах». Композитор
inception planned Phase 7.5 для introduction correlation matrix
or Bayes-network (composer.py:8-16, удалено в Phase 7.5).

### 2. Линейная сумма «срывается» при нескольких сильных SUPPORTS

Три SUPPORTS на 0.5 → raw = 1.5, clamp → 1.0. Не различает
«достаточно уверенно» (хотим ~0.85) от «уверенно почти полностью»
(хотим ~0.99). Decision-threshold у UI становится степ-функцией
вокруг trivial sums.

### 3. CONTRADICTS взаимодействуют с SUPPORTS асимметрично

Старый weight-based subtract:

- Сильное CONTRADICTS (DNA с 0.85 weight) полностью обнулит
  hypothesis даже если SUPPORTS были согласованны.
- Слабый CONTRADICTS (single-source disagreement weight=0.1)
  едва двигает score, хотя качественно «есть противоречащий
  факт» — серьёзный сигнал.

В реальности противоречия — это не просто negative weight: один
real contradiction почти всегда означает «гипотеза имеет дефект»,
который должен показать UI-warning независимо от веса.

## Рассмотренные варианты

### Вариант A — Status quo (Phase 7.4 weighted sum)

Не делать ничего, документировать ограничения.

- ✅ Ноль изменений, существующие scores валидны.
- ❌ Все три проблемы выше остаются.
- ❌ User-facing thresholds (0.85 для same_person в UI) калибруются
  к weird-shape distribution scores'ов; любое добавление rule'ов
  ломает калибровку.

### Вариант B — Per-source weighted average (no fusion)

Среднее всех weights, normalized по rule_id.

- ✅ Простая формула.
- ❌ Не различает «один strong rule» от «много weak rules».
- ❌ Контр-интуитивно: добавление weak SUPPORTS уменьшает score,
  если оно weaker среднего.

### Вариант C — Naïve Bayesian fusion на всех evidence

`P = 1 − Π(1 − p_i)` для всех supports, contradicts вычитают свой
weight как раньше.

- ✅ Sensible для независимых evidence: дополнительные SUPPORTS
  всегда увеличивают score, насыщение к 1.0 estimable.
- ❌ Игнорирует корреляцию: surname_dm_match два раза подряд
  через bucket overlap фьюзятся как два независимых факта,
  переоценивая.

### Вариант D — Hybrid Bayesian-fusion + same-source corroboration + flat contradiction penalty (выбран)

- Для SUPPORTS из *разных* rule_id: Bayesian fusion
  (assume independence — different rules, different sigals).
- Для SUPPORTS с одинаковым rule_id: weighted average (correlation
  внутри одного источника предполагается высокая).
- Для CONTRADICTS: фиксированный штраф 0.1 за единицу, capped 0.5.
  Не привязан к weight: качественный сигнал важнее численного.
- Floor 0.05 если есть хоть какое-то evidence — UI отличает «нет
  данных» от «данные есть, но слабые».

- ✅ Решает все три проблемы.
- ✅ Интерпретируемо: Bayesian fusion — стандартный pattern; flat
  penalty прост в калибровке.
- ✅ Корреляция aware на наиболее очевидном уровне (same source).
- ❌ Поведение DNA CONTRADICTS меняется: раньше strong DNA-contradiction
  обнуляла hypothesis; теперь даёт −0.1 штрафа. Mitigation: `contradiction_flags`
  в `AggregatedConfidence` возвращает список flagged rule_ids — UI
  показывает warning chips и не маскирует противоречие.
- ❌ Существующие тесты с захардкоженными expected scores нужно
  обновить (это сделано в этом PR).

### Вариант E — Full Bayesian network

Construct DAG over rules с явными conditional probabilities.

- ✅ Теоретически правильно.
- ❌ Cost: per-rule пары conditional probabilities — десятки чисел,
  никак не калибровано. Premature без datapoint'ов из real reviews.
- ❌ Outlay не оправдан: hypothesis-review backlog в текущей фазе
  ещё мал, нечем калибровать сеть.

## Решение

Выбран **Вариант D**.

### Алгоритм

1. **Группировка** SUPPORTS по `rule_id` → `dict[rule_id, list[weight]]`.
2. **Per-source corroboration:** для каждой группы — weighted average
   (в Phase 7.5 — простое среднее, т.к. evidence-weight это и есть
   self-confidence; Phase 7.6+ может ввести per-evidence reliability и
   сделать настоящий weighted average).
3. **Bayesian fusion** между группами: `P = 1 − Π(1 − w_g)`.
4. **Contradiction penalty:** count CONTRADICTS-evidence (не weight!),
   subtract `min(0.1 * count, 0.5)`.
5. **Floor:** если evidence-list непустой — `score = max(score, 0.05)`.

### Реализация

- `packages/inference-engine/src/inference_engine/aggregation.py` —
  чистая функция `aggregate_confidence(evidences) -> AggregatedConfidence`.
  Нет I/O, нет ORM. Идеально подходит для bulk-compute: O(n) по числу
  evidence, без аллокаций кроме одного `defaultdict`.
- `composer.py` делегирует в `aggregate_confidence`. ORM-схема, API
  contracts, тестовые fixtures не меняются.
- Возвращаемое `AggregatedConfidence` несёт rich breakdown:
  `composite_score`, `source_breakdown[]` (для UI explanation),
  `contradiction_flags[]` (для UI warnings), `contradiction_penalty`
  (для audit / debug).

### Persisted hypothesis migration

Persisted hypotheses держат `composite_score`, посчитанный старым
algorithm. После deploy Phase 7.5:

- Свежий `compute_hypothesis` использует v2 (через composer).
- Старые rows остаются с legacy-score, пока их не пересчитают.

Migration path — **explicit recompute endpoint**:
`POST /trees/{id}/hypotheses/recompute-scores` (owner-only). Service-
функция `recompute_all_hypothesis_scores(session, tree_id)`:

- Грузит все hypothesis-rows дерева с eagerly loaded evidences.
- Для каждой — пересчитывает score через `aggregate_confidence`
  (без ре-execute rules — мы не хотим зависеть от свежести доменных
  данных, плюс это защищает audit-trail: «evidences с момента compute
  не менялись»).
- Сохраняет новый `composite_score`.
- `reviewed_status` НЕ трогает (ADR-0021 §«Idempotency»: user judgment
  сохраняется; UI Phase 4.6+ покажет «score изменился с момента
  review» через сравнение `computed_at` vs `reviewed_at`).
- Пишет одну `AuditLog` строку на batch (не на hypothesis):
  `entity_type='hypothesis_batch_recompute'`, `action=update`, diff
  содержит `{algorithm, recomputed_count, mean_absolute_delta,
  max_absolute_delta}`.

Мы НЕ запускаем recompute автоматически на deploy: это explicit
admin action, чтобы не спамить notification-service'у уведомлениями
о «новых hypotheses» (которых не было) и не surprise'ить user'ов
изменением scores в pending review queue без warning.

### Что НЕ входит в Phase 7.5

- **Per-evidence reliability weights** для weighted average внутри
  same-source group. Сейчас — plain mean. Phase 7.6+, когда rule
  authors начнут различать «strong evidence» от «weak hint» внутри
  одного rule_id.
- **Contradiction-aware Bayesian fusion** (включить CONTRADICTS в
  formula как posterior negative). Опять — calibration data needed.
- **Multi-step inference / chained hypothesises**. Существующие
  hypotheses pairwise; multi-subject — Phase 7.x ADR.
- **HypothesisSuggester (Phase 10.0)** — LLM выдаёт single confidence
  на suggestion. Нет multi-evidence для aggregation, формула v2 не
  применима. Когда LLM начнёт ссылаться на множественные
  `evidence_refs` (Phase 10.1+ persistence layer), agent сможет
  применить `aggregate_confidence` после mapping refs → engine
  Evidence — но это отдельная feature.

## Последствия

**Положительные:**

- Composite_score лучше отражает intuition: «много слабых доказательств»
  ≠ «одно сильное», корреляция same-source не переоценивается, флэт-
  штраф за CONTRADICTS predictable.
- Floor 0.05 даёт UI стабильную «у нас есть данные» сигнал, даже
  если score близок к 0 (не нужно проверять `evidences != []` отдельно).
- `AggregatedConfidence` rich-структура — UI может показать source
  breakdown без отдельных API-вызовов (`hypothesis.evidences[]` для
  details, `aggregated.source_breakdown[]` для summary).
- Public API `aggregate_confidence` доступен для caller'ов вне
  composer (recompute pipeline, future ML-rule training, etc.).

**Отрицательные / стоимость:**

- DNA CONTRADICTS теперь даёт фиксированный штраф 0.1 вместо
  vector subtract `weight=0.85`. Это known regression: strong DNA-
  evidence «too cheap» в новой формуле. Mitigation:
  - `contradiction_flags` показывают rule_id в UI как warning chips;
  - Phase 7.6 может ввести «critical contradictions» (sex, DNA <
    threshold), у которых penalty повышенный (например, 0.3 вместо
    0.1) — как config-параметр `aggregate_confidence`.
- Calibration thresholds во UI (например, `min_confidence=0.85` в
  default GET-фильтре `/hypotheses`) теперь systematically lower —
  Bayesian насыщается медленнее. Default будет 0.5 как и сейчас (не
  меняем); UI копи и тесты, ссылавшиеся на 0.85, обновлены до 0.75.
- `RECOMPUTE_ALGORITHM_VERSION` появляется как ещё одна version-
  string в audit log'ах. Когда мы будем менять aggregation в
  будущем (Phase 7.6+), нужно бампить эту константу.

**Риски:**

- User'ы видят, что scores «уменьшились» после recompute. Mitigation:
  - audit-log diff содержит `mean_absolute_delta` — UI может показать
    «recompute изменил scores в среднем на X»;
  - threshold filtering в `/hypotheses` GET использует тот же default
    (0.5), который оба algorithm'а удовлетворяют для confident pairs.
- `aggregate_confidence` — критический hot-path в bulk-compute. В
  performance smoke-test (`test_aggregation_handles_large_evidence_
  list_quickly`) проверяем что 100 evidence считаются за < 1 ms на
  CI железе. Если будет регрессия — `O(n²)` или allocation-explosion.

**Что нужно сделать в коде (этот PR):**

- `packages/inference-engine/src/inference_engine/aggregation.py` — новый.
- `packages/inference-engine/src/inference_engine/composer.py` —
  делегирование в `aggregate_confidence`.
- `packages/inference-engine/src/inference_engine/__init__.py` —
  экспорт `aggregate_confidence` / `AggregatedConfidence` /
  `SourceContribution`.
- `packages/inference-engine/tests/test_aggregation.py` — unit edge
  cases + property tests (hypothesis library).
- `packages/inference-engine/tests/test_composer.py` — обновлены
  expected scores под v2.
- `packages/inference-engine/tests/test_integration_zhitnitzky.py`,
  `test_rules_dna.py`, `services/parser-service/tests/test_hypothesis_
  runner.py` — threshold update 0.85 → 0.75.
- `services/parser-service/src/parser_service/services/hypothesis_
  score_recompute.py` — recompute service.
- `services/parser-service/src/parser_service/api/hypotheses.py` —
  новый POST endpoint.
- `services/parser-service/src/parser_service/schemas.py` —
  `HypothesisRecomputeScoresResponse`.
- `services/parser-service/tests/test_hypothesis_score_recompute.py`
  — integration tests.

## Когда пересмотреть

- Если `mean_absolute_delta` в audit-log'ах remains > 0.15 на месяц
  после deploy — algorithm даёт заметную drift, нужен либо calibration
  pass либо новая версия aggregation.
- Если user'ы жалуются «strong DNA-contradiction не обнуляет
  hypothesis» — переходим к «critical contradictions» с увеличенным
  penalty (Phase 7.6).
- Если performance benchmark (`< 1 ms / 100 evidence`) упадёт —
  замерить hot-path и оптимизировать (вероятно, сделать
  `AggregatedConfidence` `dataclass slots=True` вместо Pydantic).

## Ссылки

- ADR-0016 — inference-engine architecture (Phase 7.0); §«Минусы»
  планировал correlation-aware aggregation на Phase 7.5.
- ADR-0021 — hypothesis persistence; idempotency-инвариант
  «сохранять reviewed_status при пересчёте».
- ADR-0028 — bulk-compute rate limiting (Phase 7.5 finalize, общая
  Phase 7.5 milestone).
- `packages/inference-engine/src/inference_engine/aggregation.py` —
  реализация.
- `services/parser-service/src/parser_service/services/hypothesis_
  score_recompute.py` — migration pipeline.
