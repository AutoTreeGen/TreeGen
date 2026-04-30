# ADR-0059: AI source extraction (Phase 10.2)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `ai`, `llm`, `parser`, `phase-10.2`, `cost`, `privacy`

## Контекст

Phase 10.0 (#130, ADR-0043) положил skeleton AI-слоя: Anthropic / Voyage
клиенты, prompt registry, kill-switch, structured output. Из use-case'ов
есть один stub — `HypothesisSuggester`, который не персистится и не
проходит через cost-gate (см. docstring `use_cases/hypothesis_suggestion.py`).

Phase 10.2 — **первый production-ready use case AI-слоя**: Claude читает
текст / PDF / изображение источника (письмо, метрика, перепись, сертификат
и т. п.) и извлекает структурированные генеалогические факты — persons,
events, places, relationships — которые пользователь review'ит и
конвертирует в реальные доменные сущности.

Phase 10.1 (hypothesis suggester production: persistence + cost-gate)
**не пройдена** на момент этого PR. Это меняет дизайн-задачу: cost-gate
и persistence patterns, которые 10.1 должна была заложить, проектируем
здесь — как **reusable модули** в `ai_layer.*`, чтобы 10.1 plug'нулась
без переделки.

Силы давления:

- **Cost.** Claude Sonnet 4.6 ≈ $3 / 1M input tokens, $15 / 1M output.
  Один документ объёмом 4 страницы = ~3k tokens input + ~1k output =
  $0.024. На дереве в 10k источников — $240, если запустить на всё.
  Без явного gate это превратится в utility-bill incident.
- **Privacy / GDPR.** Источники могут содержать DNA-data (kit summaries,
  ethnicity reports, segment lists). LLM-провайдеры логируют запросы;
  ADR-0043 §«Privacy» запрещает отправку DNA в LLM. Тип источника
  (`source_type`) — единственный structurally-доступный сигнал.
- **Quality.** OCR scans низкого качества часто содержат мусор; LLM
  должен degrade'ить gracefully, а не «домысливать» отсутствующие
  данные. Защита от галлюцинаций — отдельная hard-rule в промпте +
  Pydantic validation.
- **Reusability.** 10.1, 10.3 (RAG), 10.4 (OCR post-process) будут
  иметь те же требования: cost-gate, kill-switch, persistence shape.
  Дублирование = быстрый drift между use-case'ами.

## Рассмотренные варианты

### A. Cost-gate location

- **Reusable module `ai_layer.budget` (выбрано):** generic
  `BudgetLimits` + `BudgetReport` dataclasses + `BudgetExceededError`.
  Use-case-specific querying (counting runs / summing tokens) — caller'а,
  чтобы `ai_layer` не зависел от конкретных ORM-таблиц.
  - ✅ 10.1, 10.3, 10.4 переиспользуют без копипаста.
  - ✅ ai-layer остаётся без зависимости от sqlalchemy.
- **In parser-service helpers:** замораживает паттерн внутри одного
  сервиса; 10.1 может оказаться в inference-service и придётся
  дублировать. Отвергнуто.
- **Per-call middleware:** усложняет debug; cost зависит от модели и
  длины входа, поэтому проверка должна быть до вызова с учётом
  ожидаемого размера. Отвергнуто.

### B. Multi-pass strategy для source extraction

- **Single structured-output call с staged-prompt (выбрано):** один
  Claude-вызов, prompt просит модель сначала отметить структуру
  документа, потом перечислить entities, потом relationships, потом
  оценить confidence — всё в одном JSON. Chain-of-thought встроен в
  prompt без отдельных вызовов.
  - ✅ 1× cost / 1× latency vs. true multi-pass.
  - ✅ Claude Sonnet 4.6 хорошо держит длинный structured-output на
    стресс-тестах ru/pl/en/he корпуса (Phase 9.0-pre research).
  - ❌ Один шанс на корректный JSON — митигируется Pydantic
    `ValidationError` + caller-уровневый retry (1 раз).
- **True multi-pass (4 calls):** каждая стадия — отдельный prompt.
  - ✅ Лучше определяемые failure-modes: какая стадия упала, видно.
  - ❌ 4× cost / 4× latency. На 10k документов — $960 vs. $240.
  - ❌ Cost не оправдан до тех пор, пока единственный вызов
    демонстрирует приемлемую quality. Откладываем до 10.4 если
    понадобится.

### C. Vision + PDF strategy

- **Text-first, PDF→text через pypdf, vision как fallback на низкое
  качество text-extraction (выбрано):** 90% PDF-источников (digitised
  parish records, OCR'ed images) дают читаемый текст через pypdf. Vision
  стоит ~10× дороже чем equivalent text input (Anthropic image-tokens
  pricing). Используем vision только когда pypdf вернул мусор или
  слишком короткий текст.
  - ✅ Cost-эффективно: text-fast-path для большинства входов.
  - ✅ Vision доступен для случаев, где он действительно нужен (фото
    рукописи, низкокачественный скан).
- **Vision-only:** проще, но дороже на порядок.
- **OCR layer (tesseract) перед LLM:** добавляет ещё одну зависимость
  и failure-mode. Phase 10.4 рассмотрит, если text+vision окажутся
  недостаточными.

### D. Privacy: что не отправлять в LLM

- **`source_type == 'dna_test'` → 422 (выбрано):** structurally-доступный
  фильтр, не требует scanning контента. ADR-0043 §«Privacy» формализует
  «DNA never goes to LLM». DNA-test sources создаются GEDCOM-импортом
  из `SOUR.TYPE` или manual user input — known-safe сигнал.
- **Content scanning (regex / classifier):** дороже, склонен к
  false-negative. Не делаем.
- **User-level opt-out flag на `users`:** добавляет колонку и UI; для
  10.2a избыточно — kill-switch + DNA-source-filter покрывают сценарий
  «не хочу AI на моих данных» через `AI_LAYER_ENABLED=false` глобально
  или manual non-trigger. User-level flag — Phase 10.2b или позже,
  если появится спрос.

### E. Persistence shape

- **Two tables: `source_extractions` (run-level) + `extracted_facts`
  (per-fact) (выбрано):** run-level хранит cost, status, raw_response —
  reusable для analytics и debugging. Fact-level хранит accept/reject
  decisions per-suggestion — позволяет partial accept.
  - ✅ 10.1 будет почти зеркальной структурой (`hypothesis_runs` +
    `extracted_hypotheses`); ai_layer.runs.AIRunStatus enum уже общий.
  - ✅ Token-tracking агрегируется через SUM по
    `source_extractions.input_tokens + output_tokens`.
- **Single denormalized `ai_runs` table:** generic, но JSONB-разноструктура
  усложняет UI/querying. Откладываем до Phase 10.5+ если будет рост
  use-case'ов.

## Решение

Принимаем:

1. **Reusable submodules в `packages/ai-layer/src/ai_layer/`:**
   - `budget.py` — `BudgetLimits`, `BudgetReport`, `BudgetExceededError`,
     `evaluate_budget(report)`. Generic, без ORM-зависимостей. Default
     limits — `MAX_RUNS_PER_DAY=10`, `MAX_TOKENS_PER_MONTH=100_000`.
   - `gates.py` — `ensure_ai_layer_enabled(config)` (raise) +
     `ai_layer_enabled_dependency(get_config)` factory для FastAPI.
   - `runs.py` — `AIRunStatus` enum (`PENDING/COMPLETED/FAILED`) и
     conventions для shape `raw_response` jsonb (model, prompt_version,
     stop_reason, response_text). Не ORM, чисто схема и helpers.
2. **Use case `use_cases/source_extraction.py`:**
   - `SourceExtractor(anthropic, registry)` — async-callable, инжектится.
   - `extract_facts_from_text(text, source_metadata) -> ExtractionResult`.
   - `extract_facts_from_image(image_bytes, mime_type, source_metadata)`
     — vision path через `complete_structured_with_image`.
   - PDF-extraction (pypdf → fallback на vision при low-quality) —
     обязанность caller'а в parser-service (там же установлен pypdf),
     ai-layer не тянет PDF-зависимости.
3. **Prompt template `source_extractor_v1.md`:** Russian/Hebrew/Polish/
   Yiddish-aware, GEDCOM-aware (ABT/BEF/AFT/BET..AND даты), single-pass
   structured output (см. вариант B).
4. **ORM в `shared_models.orm`:**
   - `source_extraction.py` — `SourceExtraction(source_id, requested_by_user_id,
     model_version, prompt_version, status, input_tokens, output_tokens,
     raw_response jsonb, error, created_at, completed_at)`.
   - `extracted_fact.py` — `ExtractedFact(extraction_id, fact_index,
     fact_kind enum[person|event|relationship], data jsonb,
     confidence, status enum[pending|accepted|rejected], reviewed_at,
     reviewed_by_user_id, review_note)`.
   - Регистрируем оба в `SERVICE_TABLES` (служебные, не tree-entity).
   - Alembic 0026 миграция.
5. **API endpoints в parser-service:**
   - `POST /sources/{id}/ai-extract` — gate'ы + budget check + создание
     `SourceExtraction(status=PENDING)` + sync-вызов use-case'а.
     Sync-mode для 10.2a (typical document = 5–10s); async-arq добавим
     если станет узким местом. 422 на DNA-source.
   - `GET /sources/{id}/extracted-facts` — list пар (extraction, facts).
   - `POST /sources/{id}/extracted-facts/{fact_id}/accept` — converts
     pending fact в реальную доменную запись (Person / Event /
     Citation+relationship), provenance.ai_extraction_id = run id.
   - `POST /sources/{id}/extracted-facts/{fact_id}/reject` — set status.
6. **Cost-guard wired:** `BudgetGuard.check(session, user_id)` перед
   каждым POST /ai-extract; counting через прямой SQL на
   `source_extractions` (10.1 будет добавлять `hypothesis_runs` к
   запросу — отдельной функцией).
7. **Privacy:** `Source.source_type == 'dna_test'` → 422 «DNA sources
   cannot be sent to AI extraction (ADR-0043 §Privacy)». Nothing else
   blocks (kill-switch покрывает «вообще не хочу AI»).

## Последствия

- **Положительные:**
  - Phase 10.1 (hypothesis suggester production) plug'нется в
    `ai_layer.budget` + `ai_layer.runs` без копипаста — потеряет дни,
    не недели работы.
  - Cost-gate на каждом AI-endpoint'е по умолчанию: новый endpoint без
    `Depends(check_ai_budget)` дисциплинируется code review.
  - DNA-data structurally не достигает Anthropic API — формальное
    GDPR / ADR-0012 compliance.
  - Single-pass extraction даёт low-cost baseline; multi-pass в 10.4
    при необходимости.

- **Отрицательные / стоимость:**
  - Две новых таблицы и одна alembic миграция.
  - `pypdf` добавляется в parser-service deps (~3 МБ wheel).
  - User-level opt-out отсутствует — для 10.2a kill-switch (global) +
    manual non-trigger достаточно. Если станет нужно, добавим в 10.2b.

- **Риски:**
  - LLM может извлечь fabricated entity (имя, которого нет в источнике).
    Защита: prompt-rule «cite raw_quote из текста для каждого extract'а»
    - UI review-step (см. Phase 10.2b). Не 100% safety, но cost-balanced.
  - Sync extraction блокирует HTTP-respond на длительность одного Claude
    вызова (1–10 сек). Для batch UI приемлемо; если документ
    большой — timeout. Async-mode перенесём в 10.2c при необходимости.
  - PDF-quality fallback на vision не реализован в 10.2a (только
    text-path через pypdf). 422 если pypdf вернул < 50 chars.
    Vision — endpoint расширим в 10.2b когда frontend поддержит.

- **Что нужно сделать в коде:**
  - ✅ Этот PR (10.2a): ai_layer reusable submodules, use case, prompt,
    ORM, миграция, API, cost guard, тесты, ADR-0059.
  - 10.2b: frontend (review screen, accept/reject UI, cost indicator),
    optional vision endpoint.
  - 10.1 (parallel): hypothesis suggester production, plug'аем в
    `ai_layer.budget` + `ai_layer.runs`.

## Когда пересмотреть

- Если single-pass extraction демонстрирует < 70% precision на manual
  evaluation — переход к true multi-pass (вариант B).
- Если cost вырастет за $X/мес/user — добавить per-tier rate limits
  и/или Haiku-fallback на простых документах.
- Если появится спрос на user-level AI opt-out — добавить
  `users.ai_extraction_opt_in` колонку + UI.
- Если PDF-text-extraction quality плохая — добавить vision-fallback
  и/или OCR-pre-processing.

## Ссылки

- ADR-0043 — AI layer architecture (Phase 10.0).
- ADR-0012 — DNA privacy.
- ADR-0021 — hypothesis persistence design.
- ADR-0046 — GDPR export worker (storage abstractions для multimedia).
- Anthropic vision API:
  <https://docs.anthropic.com/en/docs/build-with-claude/vision>.
- pypdf docs: <https://pypdf.readthedocs.io/>.
