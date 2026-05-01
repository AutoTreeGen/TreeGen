# ADR-0060: AI normalization for places + names (Phase 10.3)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `ai`, `llm`, `embeddings`, `normalization`, `phase-10.3`,
  `eastern-european`, `jewish-genealogy`, `privacy`

## Контекст

Phase 7 уже даёт детерминированный entity-resolution для мест и имён
через `entity_resolution.daitch_mokotoff` (Soundex для Восточно-Европейских
/ еврейских фамилий), `place_match_score` (token_set + hierarchy
prefix), `person_match_score` (composite). Это работает на парах
**уже-существующих** в БД сущностей: «вот два кандидата из импорта,
насколько они похожи».

Что детерминированный pipeline **не делает хорошо**:

- **Cyrillic → Latin nomalization.** «Юзерин, Гомельская обл» → требует
  знать, что «обл» = oblast = регион Беларуси, что это Pale of
  Settlement территория, и что современная транслитерация — `Yuzerin`.
  Жёстко-кодировать это в Python — тысячи правил.
- **Hebrew/Yiddish → Latin.** «מאיר בן אברהם הכהן» — нужно сегментировать,
  понять `ben`-паттерн (matronymic), маркер `הכהן` (Kohanim). Никакой
  open-source библиотеки полного покрытия нет.
- **Historical context.** «Brody, Galicia, Austria» — правильная
  нормализация требует понимания, что это сейчас Украина (Львовская
  область), а в источнике — Австро-Венгрия. Hard-coded gazetteer
  типа JewishGen JOWBR / Geonames имеет 3M+ записей, но не покрывает
  Yiddish-варианты.
- **Variance в diminutives.** «Yossi» = Yosef = Joseph = Иосиф.
  Daitch-Mokotoff возвращает один phonetic-bucket, но не знает
  «Yossi → Yosef» как лексическую трансформацию.

Phase 10.3 — **первый AI-нормализатор**. Бьём по двум use case'ам:

1. **Place normalization.** GEDCOM-импорт даёт сырые `PLAC` строки;
   ARM gazetteer недостаточен для Eastern European корпуса. Имеем
   ИИ возвращать структурированный `Place` (canonical_name,
   country_modern, country_historical, settlement, lat/lon при
   уверенности, ethnicity_hint, alternative_forms).
2. **Name normalization.** GEDCOM `NAME` строки приходят в Cyrillic,
   Hebrew, Latin (с диакритикой) — модель должна разделить на
   given/surname/patronymic, выдать transliteration_scheme,
   surface alternative latin-варианты для phonetic-bucket'ов
   inference-engine, отметить Kohen/Levi/Israelite **только при
   явных on-text признаках**.

Parallel — Voyage embeddings (выбран в ADR-0043 §B как multilingual
retrieval-tuned 1024-dim) даёт ranked match нормализованной формы
к existing canonical-кандидатам пользователя — это закрывает
«посмотри, не есть ли это уже у меня».

Cost-guards must be aligned с Phase 10.2 (ADR-0059): same
`AI_LAYER_ENABLED` kill-switch, same per-user-day rate limit /
per-user-month tokens budget contract (`ai_layer.budget`).

Силы давления:

- **Privacy.** Имена живых людей — PII; place + date вместе
  идентифицируют. Beta-only до consent-toggle (Phase 4.10b),
  не для public-tree share (ADR-0047).
- **Cost.** Нормализация — высокочастотный use case (один
  GEDCOM-импорт = десятки/сотни мест и имён). Если каждый стоит
  $0.01 за вызов — на крупном дереве это $50-100 за импорт.
- **Determinism.** Нормализация должна быть deterministic-ish
  для тестируемости (temperature=0, retry on malformed JSON).
  Существующий phonetic / token-bucket остаётся authoritative
  для дедупа — AI лишь обогащает.

## Рассмотренные варианты

### A. Что генерирует LLM — single-pass JSON vs multi-pass tool-calling

- **Single-pass structured JSON (выбрано):** LLM получает raw-строку
  - опциональный context, возвращает один Pydantic-валидируемый JSON
  c полями `canonical_name` / `given` / etc.
  - ✅ Один вызов = одна нормализация → предсказуемая стоимость.
  - ✅ Контракт через Pydantic — `model_validate_json` ловит drift'ы.
  - ✅ Совместимо с Phase 10.1/10.2 паттернами.
  - ❌ Нет интерактивного «уточни в 2-х шагах». Acceptable: caller
    делает 2 запроса вручную если нужно.
- **Tool-calling (gazetteer lookup):** LLM может позвать `lookup_place`
  как tool. Открывает дверь к JewishGen / Geonames. Отвергнуто на 10.3:
  внешних gazetteer adapters ещё нет (ADR-0009 §«Public APIs»),
  добавлять их + tool-calling вместе = scope creep. Phase 10.x.
- **Multi-pass:** stage1 → классификация script, stage2 → транслитерация,
  stage3 → enrichment. Дороже (3× tokens) и не даёт качества больше:
  Sonnet 4.6 справляется в один pass на наших стресс-тестах.

### B. Voyage candidate matching — что эмбеддить

- **Только `canonical_name` для places, `given+surname` для names
  (выбрано):**
  - ✅ Один short string per item → дёшево и быстро.
  - ✅ Voyage `voyage-3` multilingual; даже если caller передаёт
    Cyrillic candidates, vectors совмещаются с Latin query.
  - ❌ Не учитывает `country_modern` / `admin1` для places — два
    разных Brody в разных странах могут смешиваться. Mitigation —
    caller pre-фильтрует candidates по country (`MAX_CANDIDATES`
    в use-case).
- **Multi-field embedding (canonical_name + admin1 + country):**
  каждое поле в отдельную embedding с весами — Voyage биллит за
  3× токены, не оправдано на 10.3.
- **pgvector-search в БД:** Phase 10.x — добавим колонку
  `places.embedding vector(1024)` + index. Сейчас не делаем,
  чтобы избежать миграции (Phase 10.3 — pure-addition; ADR-0043
  §«Embedding cache» откладывает).

### C. Storage cost telemetry — Redis vs ORM table (повтор ADR-0057/0059)

- **Redis day-bucket counters (выбрано):**
  - ✅ Ноль миграций. Phase 10.3 не блокируется на schema.
  - ✅ Counter-based (INCR / INCRBY) — атомарные операции.
  - ✅ TTL = 24h+buffer для runs, 30d+buffer для tokens — самоочистка.
  - ❌ Нет per-call audit trail (только агрегаты). Acceptable до 10.5.
- **ORM `ai_normalization_runs` table:** дублировал бы 10.2
  `source_extractions` без сильных причин — нормализация idempotent,
  ничего не персистируется в доменной модели (отличие от
  source-extraction, который порождает domain-entities через review).
  Отвергнуто.
- **`log_ai_usage` Redis LIST из 10.1:** есть, но это global LIST —
  query по user'у требует `LRANGE 0 -1 | filter`, O(N) на каждый
  request. Counter'ы для budget — отдельная структура; LIST остаётся
  для per-call audit (Phase 10.5 миграции в Postgres).

### D. Tribe markers (Kohen/Levi) — explicit-only vs heuristic

- **Explicit-only (выбрано):** prompt требует, чтобы `tribe_marker`
  был `kohen`/`levi` ТОЛЬКО при on-text признаках (`HaKohen`,
  `הכהן`, фамилия `Cohen` AND контекст). Default — `unknown`.
  - ✅ False-positive cost очень высокий: ошибочно пометить
    person'а как Kohen — обидное искажение генеалогической
    истории.
  - ✅ Cohen / Levy / Goldstein как surnames часто occupational /
    migrant без priestly descent — ADR-0015 §Daitch-Mokotoff
    отдельно отмечает.
  - ❌ Recall ниже: пропускаем неявные случаи. Acceptable: lost
    information vs. wrong information выбор очевиден для
    еврейской генеалогии.
- **Heuristic fallback (`Cohen` surname → kohen):** отвергнуто.

### E. Failure mode на Redis-сбое для budget'а — fail-open vs fail-closed

- **Fail-open (выбрано):** если `compute_normalize_budget_report`
  не смог прочитать Redis, возвращаем zero-usage отчёт; нормализация
  продолжается.
  - ✅ Redis-сбой = инфраструктурный (всё запущено в одном кластере);
    блокировать AI-эндпоинты из-за telemetry-проблемы — over-reaction.
  - ✅ Лимиты низкие (10/day) → даже окно «без budget guard» на
    короткий sub-минутный outage не сожжёт значимого budget'а.
  - ❌ Теоретически atypical user может выжать > 10 calls пока
    Redis лежит. Acceptable до Phase 10.5 (биллинг с реальными
    деньгами; тогда — fail-closed + Sentry alert).
- **Fail-closed (503 при Redis-сбое):** Phase 10.5 default,
  слишком жёстко на 10.3.

## Решение

Выбраны: **A — single-pass JSON**, **B — single-field embedding,
caller pre-фильтрует candidates**, **C — Redis day-bucket counters**,
**D — explicit-only tribe markers**, **E — fail-open**.

Реализация:

- `packages/ai-layer/src/ai_layer/use_cases/normalize.py` —
  `PlaceNormalizer` / `NameNormalizer` use cases с retry / fail-soft /
  dry-run / Voyage candidate-match.
- `packages/ai-layer/src/ai_layer/prompts/{place,name}_normalizer_v1.md` —
  Eastern European Jewish genealogy specifics (Pale of Settlement,
  patronymics, BGN/PCGN+YIVO+ALA-LC, Kohen/Levi rules).
- `packages/ai-layer/src/ai_layer/types.py` — `PlaceNormalization`,
  `NameNormalization`, `CandidateMatch`, `NormalizationResult`,
  domain-shared `Literal`-aliases (`ScriptLabel`, `EthnicityHintLabel`,
  `TribeMarkerLabel`).
- `services/parser-service/src/parser_service/services/ai_normalization.py` —
  Redis-based budget helpers + orchestrators.
- `services/parser-service/src/parser_service/api/normalize.py` —
  `POST /places/normalize`, `POST /names/normalize`.
- `services/parser-service/src/parser_service/schemas.py` —
  Pydantic schemas для request/response.

## Последствия

### Положительные

- AI-нормализация добавляется без миграций БД и без изменений в
  доменных таблицах: `places.canonical_name` / `names.given_name`
  и т.п. остаются прежними. Caller (UI) сам решает, как мапить
  AI-вывод на ORM (apply / suggest / discard).
- Use cases можно вызывать как из endpoint'ов, так и из background
  jobs (например, bulk re-normalize всех мест tree'а после import'а).
- Cost-guards выровнены с 10.2: тот же `AI_LAYER_ENABLED`, тот же
  `BudgetLimits`, тот же `evaluate_budget`. Phase 10.5 биллинг
  будет считать кросс-use-case usage без переписывания.

### Отрицательные / стоимость

- **Cost оценка:** ~600 input + ~200 output tokens per call.
  Sonnet 4.6 ($3/$15 per MTok) → **$0.0048/call**. Если caller
  передаёт candidates → +Voyage embed (1 query + N candidates),
  voyage-3 ($0.18/MTok) → ~$0.0001-0.0003/call. **Total ≈ $0.005/call**
  (target из user spec ≤ $0.01 — выполнено).
- **Privacy debt:** имена / места живых людей улетают в Anthropic.
  Beta-only — необходимо включить explicit consent toggle до
  публичного rollout'а.
- **Voyage candidate match не учитывает country/admin context** —
  caller отвечает за pre-фильтрацию.

### Риски

- **Tribe-marker false-negatives:** `tribe_marker="unknown"` по
  default'у. Если пользователь хочет, чтобы AI агрессивно угадывал
  Cohen/Levy — он будет недоволен. Mitigation: документировать
  поведение в UI tooltip'е, оставить опцию ручного override.
- **Voyage rate limits:** при bulk-нормализации (200 мест × N candidates)
  можно упереться в Voyage rate limit. Mitigation: caller-уровневый
  batch (`bulk_normalize` job — Phase 10.x).
- **LLM hallucinations** на coordinates: prompt требует `null` при
  неуверенности, но edge-case'ы возможны. Mitigation: caller-side
  валидатор (Phase 10.x): сравнить `(lat, lon)` с known
  `places.canonical_name` rows, отклонить если расхождение > 50 км.

### Что нужно сделать в коде

- Эта фаза: реализовано в `packages/ai-layer/` + parser-service
  endpoints.
- Phase 10.x: bulk-normalize background job в parser-service
  (вызывает use cases для всех `places.canonical_name` / `names`
  без `romanized` поля).
- Phase 10.x: добавить `places.embedding vector(1024)` + alembic
  migration; перенести candidate-match с caller-supplied списка
  на pgvector `<->` query.
- Phase 10.5: миграция Redis-budget на ORM-aggregated.

## Когда пересмотреть

- **Cost > $200/month** на normalize → переключиться на Haiku 4.5
  для простых строк (caller-уровневая classification: «есть Cyrillic
  — Sonnet, чисто Latin без markers — Haiku»).
- **Жалоба от GDPR-аудита** по поводу PII-leakage в LLM logs →
  redaction layer перед вызовом (тот же фронт, что и для 10.1/10.2).
- **Tribe-marker false-positive** в проде (хоть один Sentry-issue) →
  усилить hard rule в prompt'е, добавить pre-validation.
- **Запрос на bulk-normalize UI** (пользователь хочет «нормализуй все
  места моего дерева одним кликом») → background job с rate-limit,
  ADR-0028 паттерн.

## Ссылки

- Связанные ADR: ADR-0043 (AI layer architecture), ADR-0057
  (hypothesis explanation — privacy/cost-guard precedent),
  ADR-0059 (source extraction — budget/runs reusable submodules),
  ADR-0015 (entity resolution — Daitch-Mokotoff), ADR-0008/0017
  (transliteration / FS gazetteer references), ADR-0009 (genealogy
  integration — JewishGen JOWBR / JGFF references).
- ROADMAP §14.1 use cases #1 (Document analyzer — частично
  пересекается через place strings) и общий genealogy-domain
  принцип CLAUDE.md §3.7 (Domain-aware).
- BGN/PCGN romanization: <https://www.gov.uk/government/publications/romanization-systems>
- YIVO Yiddish romanization: <https://www.yivo.org/transliteration>
- ALA-LC Hebrew romanization: <https://www.loc.gov/catdir/cpso/romanization/hebrew.pdf>
- Daitch-Mokotoff Soundex: <https://www.avotaynu.com/soundex.htm>
