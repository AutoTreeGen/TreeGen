# ADR-0030: AI layer architecture (когда и как применяем LLM)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `llm`, `cost`, `privacy`, `phase-10`

## Контекст

CLAUDE.md §6 фиксирует архитектурный принцип:

> **Deterministic > magic.** LLM применяется только там, где он реально полезен (см. Фаза 10). Базовые операции — детерминированные.

ROADMAP §14 описывает Phase 10 use-cases (document analyzer, translator,
research assistant, hypothesis explainer и т.п.). Phase 10.0 — это
**skeleton**: тонкий wrapper над Anthropic Claude API + 1–2 high-value
LLM-операции, чтобы зафиксировать pattern для будущих use-cases (Phase 10.1+).

Без явного ADR'а каждая последующая Phase 10.x будет переоткрывать
вопросы:

- Когда вообще звать LLM, а когда — rule-based?
- Где лимит расходов? Что делать, если impo'rt 50K-персонного дерева
  попытается «канонизировать» каждое место — это $$ + минуты latency.
- Какие данные **никогда** нельзя отправлять во внешний API (DNA-сегменты,
  PII без consent)?
- Как делать caching? Где хранить промпты и их версии?

## Рассмотренные варианты

### Вариант A — LLM «по умолчанию», rule-based fallback

- ✅ Maximum quality на каждом запросе.
- ❌ $$$. Импорт типичного 50K-персонного GED → ~10K мест → $50–500 за
  один импорт. Не проходит по бизнес-модели (см. ROADMAP §16 тарифы).
- ❌ Latency: 200–800мс на вызов × 10K = часы простоя.
- ❌ DNA-rule's и phonetic-matching стали бы зависеть от внешнего API
  (тестируемость, offline-режим).

### Вариант B — LLM **только** в gray-zone случаях, rule-based primary

- ✅ Cost-bounded: rule-based решает >90% случаев слам-данк, LLM
  привлекается для оставшихся <10%.
- ✅ Latency: hot path (slam-dunk) остаётся синхронным.
- ✅ Тестируемость: rules детерминистичны, LLM-rule изолирован за
  callable-injection → mock-able в тестах.
- ❌ Чуть сложнее в реализации (gating logic).
- ❌ Не покроет все Phase 10 use-cases — например, document analyzer и
  hypothesis explainer не имеют «rule-based» альтернативы; для них
  LLM — primary path. Это ОК: они в любом случае сидят за explicit
  user-action («объясни эту гипотезу»), не вызываются автоматически на
  каждой записи.

### Вариант C — Self-hosted LLM (Llama / Mistral)

- ✅ Контроль над privacy: данные не покидают инфраструктуру.
- ❌ Качество reasoning по русско-польско-идишским historical-texts
  заметно ниже Claude/GPT-4-class на наших data-types.
- ❌ Operational cost (GPU-hosting) — для MVP сравним с API.
- ❌ Маркеры эксклюзивно для Phase 12+, когда вопрос cost станет острым.

## Решение

Выбран **Вариант B**:

1. **Rule-based — primary path.** LLM привлекается только в gray-zone
   случаях, где детерминистический алгоритм неоднозначен.
2. **Cost ceiling.** Per-tree budget — `parser_service.config.llm_max_calls_per_import`
   (default `100`). Достижение лимита → импорт продолжается без LLM-rules,
   при этом в audit-log записывается warning «budget exhausted».
3. **Caching.** Все LLM-вызовы кэшируются по ключу
   `sha256(prompt_template_version + canonical_inputs_json)` в Redis с
   TTL **30 дней**. Дубль-вызовы (одно и то же место/имя в нескольких
   деревьях) бесплатны после первого hit.
4. **Privacy.**
   - **Никогда не отправляем в LLM:**
     - сырые DNA-сегменты, rsids, genotype matrices;
     - persistent PII без явного user-consent на «AI features»;
     - содержимое загруженных приватных документов до tier-проверки.
   - **Можно отправлять (при общем consent на AI features):**
     - имена и места из публичной части дерева;
     - метаданные источников (заголовки, даты, библиографические ссылки);
     - structured факты, которые user уже опубликовал в общедоступном
       режиме.
5. **Promпт-versioning.** Шаблоны живут в
   `packages/llm-services/src/llm_services/prompts/*.txt` с заголовком
   `# version: vN`. Версия записывается в audit-log с каждым вызовом.
6. **Default model — `claude-sonnet-4-6`.** Для большинства use-cases
   (нормализация мест, group'ировка имён) Sonnet 4.6 даёт лучший
   intelligence/cost trade-off. Hypothesis explainer (Phase 10.4) и
   long-context document analyzer (Phase 10.2) могут точечно поднять
   до Opus 4.7.
7. **Async-only API.** Все LLM-операции возвращают `coroutine`. Sync-bridge
   (если понадобится для пакетных скриптов) — задача caller'а
   (`asyncio.run(...)`).

### Score interpretation для inference-engine

LLM-rule (Phase 10.0 — `LlmPlaceMatchRule`) выдаёт Evidence только если:

- rule-based score попал в **узкую** gray-zone полосу `[0.40, 0.70]` —
  снаружи неё уже есть rule-based vердикт;
- LLM `confidence ≥ 0.50` — иначе шум, не сигнал;
- weight = `0.30 × confidence` (SUPPORTS) или `0.25 × confidence`
  (CONTRADICTS) — мягче, чем у rule-based, потому что LLM может
  галлюцинировать. Композер видит дополнительный сигнал, но не
  определяющий.

## Последствия

**Положительные:**

- Импорт 50K-tree с Phase 10.0 включённым LLM-place-rule стоит
  ≤$0.05 (≤100 LLM-вызовов × ~$0.001/вызов). Соответствует тарифной
  модели Beginner/Advanced.
- Rules остаются pure-functions — все existing tests продолжают работать
  без mock'инга API.
- Privacy boundary явный — code-review сфокусирован на "что попадает в
  LLM-промпт", не на «что вообще делается».

**Отрицательные / стоимость:**

- Дополнительная сложность для разработчика: нужно помнить о injection
  callable'а в `LlmPlaceMatchRule`. Default `None` смягчает (zero-cost
  по умолчанию).
- Caching через Redis добавляет дополнительный сервис в hot-path
  inference (Phase 10.1 task — Redis-cache layer для LLM-вызовов).

**Риски:**

- Если budget-лимит exhausted на половине импорта, у части записей
  будет LLM-evidence, у части — нет. Это создаёт асимметрию в quality
  гипотез. Mitigation: при exhaust'е писать один warning и **не**
  пытаться частично догнать в фоне (создаст confusing UX). Phase 10.1
  может добавить «AI-рефреш» как explicit user-action.
- LLM-провайдер (Anthropic) — single point of dependency. Mitigation:
  все LLM-rules опциональны (default `None`), система продолжает работать
  как rule-based-only, если ANTHROPIC_API_KEY не настроен.

**Что нужно сделать в коде (Phase 10.0):**

- `packages/llm-services/` — новый workspace member.
- `packages/inference-engine/src/inference_engine/rules/llm_place.py` —
  gray-zone gated LLM rule.
- `.env.example` — `ANTHROPIC_API_KEY` placeholder (уже добавлен).
- ROADMAP §14 — отметить Phase 10.0 done.

**Что отложено в Phase 10.x:**

- Redis-caching слой для LLM-вызовов (Phase 10.1).
- Audit-log таблица в БД (`llm_calls`) с полями
  `(prompt_version, input_hash, output_hash, tokens_in, tokens_out,
  cost_usd, tree_id, created_at)` — Phase 10.1.
- Cost dashboard (Phase 10.x).
- Free-text source extraction (Phase 10.2).
- OCR post-processing (Phase 10.3).
- Hypothesis explainer (Phase 10.4).

## Когда пересмотреть

- Если cost per-tree-import превысит $0.50 на типичной нагрузке.
- Если появится self-hosted Claude-class модель (Phase 12+ task).
- Если появится regulatory pressure на обязательное on-prem
  LLM-инференс (например, для EU enterprise tier).
- При запуске Phase 10.4 hypothesis explainer пересмотреть default
  model — Opus 4.7 может быть default для long-form rationale.

## Ссылки

- Связанные ADR: ADR-0012 (DNA privacy — privacy boundary для LLM
  pipeline'а), ADR-0016 (inference-engine architecture — protocol pure
  functions, к которому LLM-rule подключается через callable injection),
  ADR-0026 (arq background jobs — будущие background LLM-задачи).
- ROADMAP §14 «Фаза 10 — AI слой».
- CLAUDE.md §6 «Deterministic > magic».
- Anthropic prompt-caching docs:
  `https://platform.claude.com/docs/en/build-with-claude/prompt-caching`.
