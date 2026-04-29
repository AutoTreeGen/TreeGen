# ADR-0043: AI layer architecture (Phase 10.0)

- **Status:** Accepted
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `ai`, `llm`, `embeddings`, `phase-10.0`

## Контекст

Phase 10 в `ROADMAP.md` декларирует «применить LLM там, где он реально
полезен, не везде». До этого момента в репозитории не было ни одной
зависимости от AI-провайдеров — `anthropic`, `voyageai` и им подобные
SDK отсутствовали. Это было сознательно: ADR-0016 §«Pure functions без
I/O» зафиксировал, что rule'ы inference-engine — детерминированные,
без LLM-вызовов в Phase 7.x.

Phase 10.0 — **skeleton**, а не полная интеграция. Цель — приземлить
зависимости, прописать паттерны (клиенты, prompt registry, structured
output), оставить use-case stub'ы. Это даёт downstream-фазам
(10.1 hypothesis suggestions, 10.2 evidence synthesis, 10.3 RAG, 10.4
OCR post-processing) common foundation, не размазывая HTTP-клиенты по
сервисам.

Закрытый PR #115 (`feat(llm-services): AI layer skeleton`) реализовал
часть этого скоупа под именем `packages/llm-services/` и с другой
структурой (плоский layout, sync-callable injection в inference-engine,
1 use-case = 1 файл-модуль). PR был closed после merge-конфликтов с
post-#117 main; переисполнение этой ветки — этот PR.

Силы давления:

- **Cost.** LLM-вызовы — самая дорогая статья эксплуатации. Любой случайный
  вызов из dev/CI среды должен быть невозможен «по умолчанию», иначе
  utility bill вырастет молча.
- **Privacy.** ДНК-сегменты — special category (GDPR Art. 9). LLM-провайдеры
  логируют запросы; ДНК-данные не должны попасть в LLM. Имена и места —
  только с consent (политика consent — Phase 4.x, ADR-0012).
- **Determinism.** CLAUDE.md §3.6 «Deterministic > magic»: LLM применяется
  там, где он реально полезен. Базовые операции (entity resolution,
  inference rules) остаются детерминированными.
- **Versioning.** Промпты — first-class артефакт. Ремонт промпта = семантический
  breaking change для downstream-сервисов. Нужна стратегия версионирования.

## Рассмотренные варианты

### A. Anthropic vs OpenAI vs Mistral vs локальные модели

- **Anthropic Claude (выбрано):**
  - ✅ Самый длинный context (1M tokens на Opus 4.7) — критично для RAG
    над большими генеалогическими корпусами.
  - ✅ Strong на multilingual reasoning (русский / польский / иврит /
    идиш — ключевые языки восточно-европейской генеалогии).
  - ✅ Структурированный output через `response_format=json_object` и
    tool-calling — purchase нашему контракту (Pydantic-модели).
  - ✅ Prompt caching (5-минутный TTL) → дешевле RAG-вызовов в Phase 10.1+.
  - ❌ Один vendor → vendor lock-in. Митигируем через wrapper:
    `AnthropicClient.complete_structured(..., response_model=T)` —
    интерфейс провайдер-агностичен; смена провайдера = новый клиент,
    тот же контракт.
- **OpenAI GPT-4o:** comparable quality, но context короче (128k), и
  OpenAI privacy-policy слабее (data retention 30 дней по умолчанию для
  zero-data-retention контрактов нужен enterprise tier).
- **Mistral / локальные:** недостаточная multilingual quality для нашего
  domain'а; самохостинг — отдельная инфраструктурная нагрузка, не оправданная
  на Phase 10.0.

### B. Voyage AI vs OpenAI embeddings vs Cohere

- **Voyage AI (выбрано):**
  - ✅ `voyage-3` — 1024-dim, multilingual, обучена на retrieval — лучше
    OpenAI `text-embedding-3-small` на не-английских корпусах (Voyage
    benchmarks 2026-01).
  - ✅ Совместима с `pgvector(1024)` без overhead на 3072-dim векторы
    OpenAI text-embedding-3-large.
  - ✅ Поддержка `input_type` namespace (`document` vs `query`) — даёт
    лучший recall на retrieval (не нужно cross-product между query и doc).
- **OpenAI embeddings:** 3072-dim тяжелее в индексации; русско/польский recall
  хуже на наших стресс-тестах (Phase 9.0-pre research).
- **Cohere:** comparable quality, но pricing-tier hostile к
  «пробуй и плати по факту»: minimum commit'ы делают эксперименты дорогими.

### C. Prompt versioning strategy

- **Версия в имени файла (выбрано):** `hypothesis_suggester_v1.md`,
  `_v2.md` — оба остаются в репо. `PromptRegistry` экспонирует обе
  версии как именованные константы → A/B тестирование, rollback без
  миграций.
  - ✅ Read-only артефакт под git — diff'ы видны в PR.
  - ✅ Никаких миграций БД для промптов на Phase 10.0–10.2.
  - ❌ Не позволяет A/B без redeploy. Acceptable: эксперименты редкие.
- **Версии в БД:** Phase 10.5+, когда нужен runtime-свитч между версиями
  без релиза. До тех пор — overkill.
- **Single-version overwrite:** ломает downstream-контракт — отвергнуто.

### D. Embedding cache (Phase 10.0 vs 10.1)

- **Не делать таблицу на Phase 10.0 (выбрано):**
  - ✅ Skeleton — pure addition, ноль миграций → ноль риска CI-конфликта
    с другими активными фазами.
  - ✅ Voyage биллинг — за токены, не за вызовы; in-batch dedup
    (`VoyageEmbeddingClient._dedup`) уже даёт значительную экономию для
    типичных входов (повторяющиеся имена / места).
  - ❌ Нет переиспользования векторов между батчами / процессами.
    Acceptable: Phase 10.0 не делает массовых embedding-индексаций.
- **Сделать таблицу `embedding_cache` сейчас:** добавляет миграцию
  (alembic 0021 после Stripe 0020) + ORM model + schema_invariants entry.
  Срок жизни записей нужно проектировать (TTL? hard cap?), что не
  определено до появления реального use-case в Phase 10.1. Откладываем.

### E. Boundary: deterministic vs LLM

CLAUDE.md §3.6 формулирует принцип, ADR-0043 переводит его в правила:

- **LLM не вызывается из rule'ов inference-engine.** Rule'ы Phase 7.x
  остаются pure-functions (см. ADR-0016).
- **LLM-augmented rules (Phase 10.x):** живут в отдельных пакетах
  (`ai_layer.use_cases.*`), регистрируются в registry inference-engine
  через адаптер, который инжектит async-callable. Caller-уровень
  (parser-service) решает, вызывать LLM-вариант или нет (cost gate).
- **Gray-zone gating:** LLM применяется только когда детерминированный
  signal неоднозначен (например, fuzzy place-match score ∈ [0.4, 0.7]).
  Slam-dunks обрабатывает rule-based; LLM экономится для пограничных
  кейсов. Это паттерн из закрытого PR #115; он сохраняется как ориентир
  для Phase 10.1+, но в skeleton'е не реализован.
- **Defense against fabricated citations:** каждый use-case валидирует,
  что `evidence_refs` от LLM содержат только ID из input'а
  (`HypothesisSuggester._validate_evidence_refs`). Это структурная защита
  от галлюцинаций — дёшево, ловит самые частые failure mode'ы.

## Решение

Принимаем:

1. **Провайдеры:** Anthropic Claude (LLM) + Voyage AI (embeddings).
   Wrapper-API провайдер-агностичен.
2. **Структура пакета:** `packages/ai-layer/` с подпапками
   `clients/`, `prompts/`, `use_cases/`. Public API — через
   `ai_layer/__init__.py`.
3. **Master kill-switch:** `AI_LAYER_ENABLED=false` (default). Любой
   API-вызов с `enabled=false` → `AILayerDisabledError`.
4. **Структурированный output:** Pydantic-модели в `ai_layer.types`
   (`HypothesisSuggestion`, `EmbeddingResult`); SDK возвращает JSON,
   wrapper парсит через `model_validate_json` — fail-fast на нарушении
   контракта.
5. **Prompt versioning:** `{name}_v{n}.md`, оба формата хранятся в
   `ai_layer/prompts/`, доступ через `PromptRegistry.<NAME>_V<n>`.
   Jinja2 + `StrictUndefined` — забытая переменная = ошибка рендера.
6. **Embedding cache:** in-batch dedup сейчас; persistent cache —
   Phase 10.1+, отдельной миграцией.
7. **Deterministic-vs-LLM boundary:** rule'ы inference-engine остаются
   pure; LLM-augmented rules — отдельные адаптеры, gated на cost.
8. **Privacy:** ДНК-сегменты не отправляются в LLM **никогда**.
   Имена/места — только с consent (политика — ADR-0012).

## Последствия

- **Положительные:**
  - Downstream-фазы 10.1+ имеют единую точку расширения, не дублируют
    HTTP-клиенты.
  - `enabled=false` по умолчанию — CI и dev-окружения без API-ключей
    безопасны: pet вызов не сломает сборку и не сожжёт деньги.
  - Pydantic-контракты на ответ LLM ловят drift промптов на стадии
    парсинга, не в продовой логике.

- **Отрицательные / стоимость:**
  - Две новые зависимости (`anthropic`, `voyageai`) — расширение
    transitive-graph. Митигируем — обе SDK официальные, поддерживаются.
  - Prompt-файлы становятся частью domain-логики и требуют ревью
    (как код). Принимаем.

- **Риски:**
  - Vendor lock-in на Anthropic смягчён wrapper'ом, но не устранён:
    promptы написаны под Claude. Перенос на другой провайдер потребует
    повторной prompt-engineering работы. Acceptable risk на Phase 10.x.
  - Hallucinated evidence_refs — митигировано валидацией
    (`FabricatedEvidenceError`), но не на 100%: если LLM вернёт валидный
    но неверный ID, фильтр пропустит. Финальная защита — manual review
    предложенных гипотез на UI (Phase 10.2+).

- **Что нужно сделать в коде:**
  - ✅ Этот PR: skeleton-пакет, prompt registry, use-case stub,
    конфиг-loader, тесты.
  - Phase 10.1: интеграция `HypothesisSuggester` в parser-service
    (cost gate, audit log, persistence предложенных гипотез).
  - Phase 10.1+: persistent `embedding_cache` таблица (alembic
    миграция, ORM model, schema_invariants entry).
  - Phase 10.2+: RAG-pipeline над `notes` / `sources` через Voyage
    embeddings + pgvector.

## Когда пересмотреть

- Если Anthropic меняет SDK-контракт несовместимо или прекращает
  предоставление API.
- Если в Phase 10.2+ обнаружится, что Voyage embeddings проигрывают
  альтернативе на стресс-тестах нашего корпуса.
- Если LLM-augmented rules выйдут за пределы 100 вызовов / import —
  тогда нужен per-import budget enforcement (см. PR #115 §«Cost
  ceiling»).
- Если Phase 10.5 потребует runtime-A/B prompt-версий — переход к
  таблице `prompts` в БД.

## Ссылки

- Связанные ADR: ADR-0012 (DNA privacy), ADR-0016 (inference-engine
  pure-functions), ADR-0021 (hypothesis persistence).
- Closed PR #115 — оригинальная попытка skeleton'а под именем
  `llm-services`; reference для cost-gating и LLM-augmented rules
  в Phase 10.1+.
- Voyage AI docs: <https://docs.voyageai.com/>.
- Anthropic SDK docs: <https://docs.anthropic.com/en/api/client-sdks>.
