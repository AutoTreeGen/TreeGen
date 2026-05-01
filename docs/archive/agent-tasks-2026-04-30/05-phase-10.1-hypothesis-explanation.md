# Agent 5 — Phase 10.1: AI hypothesis explanation

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (`F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md`.
2. `ROADMAP.md` — «Фаза 10 — AI слой» (§14), особенно §14.1 use cases.
3. ADR Phase 10.0 (последний commit `6e25485 feat(ai-layer): phase 10.0 — AI layer skeleton (Anthropic + Voyage)`) — изучи `packages/ai-layer/` целиком: какой клиент уже есть, как заданы prompt-шаблоны, как считается стоимость.
4. `packages/inference-engine/` или ORM-модели гипотез в `packages/shared-models/` — что такое `Hypothesis`, какой evidence-graph сейчас приходит к нему.
5. Phase 4.9 hypothesis review UI — как пользователь сейчас видит гипотезы.

## Задача

Реализовать **первый продакшн use case AI-слоя**: естественно-языковое объяснение, **почему два человека — same_person**, на основе evidence-graph гипотезы. Bilingual (en/ru).

## Scope

### `packages/ai-layer/`

- Новый use case `explain_hypothesis.py`:
  - `async def explain_hypothesis(hypothesis: HypothesisInput, locale: Literal["en", "ru"] = "en") -> HypothesisExplanation`
  - `HypothesisInput` — Pydantic: `subjects` (2 person summaries), `evidence` (list of evidence items с `rule_id`, `confidence`, `details`: name match, date proximity, place proximity, DNA segments, source citations).
  - `HypothesisExplanation` — Pydantic: `summary` (1-2 предложения), `key_evidence` (top-3 пункта в порядке силы), `caveats` (что НЕ совпадает или вызывает сомнение), `confidence_label` (low/medium/high), `tokens_used`, `cost_usd`.
- **Prompt template** в `packages/ai-layer/src/ai_layer/prompts/hypothesis_explanation.py`:
  - System prompt: роль («senior genealogist with statistical training»), формат вывода (JSON с полями выше), правила («cite evidence, не выдумывай факты, отдельно перечисли caveats, если confidence < 0.7 — явно скажи что слабо»).
  - Few-shot 2 примера (один сильный match, один слабый) — синтетика, без реальных персон.
  - User-prompt: serialized evidence + locale instruction.
- **Cost telemetry**: каждый вызов логирует в новый файл `packages/ai-layer/src/ai_layer/telemetry.py`:
  - `log_ai_usage(use_case: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float, user_id: UUID | None, request_id: UUID)`.
  - **Storage в этой итерации — Redis list `ai_usage:log` с `LPUSH` + 30-day expire** (НЕ таблица в БД — это Phase 10.5). Пометка TODO в коде что мигрируем в БД при появлении фичи биллинга.
- **Dry-run mode**: env-var `AI_DRY_RUN=true` → возвращает зашитый mock-ответ без вызова Anthropic. Используется в локальной разработке без ключа.

### Без эндпоинта в этой итерации

API-эндпоинт `POST /hypotheses/{id}/explain` интегрируем в `parser-service` отдельным мини-PR после mерджа этой задачи. **Сейчас только библиотека + тесты.**

### ADR-0057 в `docs/adr/`

- Hypothesis explanation как первый use case (low risk: read-only, не меняет данные).
- Anthropic Claude Sonnet 4.x как baseline (можно расширить до Opus для high-confidence гипотез).
- Cost target: < $0.01 за запрос (~3000 input tokens × $3/MTok + ~500 output × $15/MTok ≈ $0.016 — нужно урезать prompt, либо принять).
- Privacy: какие поля evidence НЕ должны улетать в Anthropic (точные адреса? — обсудить, на этой итерации передаём всё, но логируй PII redaction как TODO).

## Тесты (> 80%)

- `packages/ai-layer/tests/test_explain_hypothesis.py`:
  - С `AI_DRY_RUN=true` — возвращается mock, формат правильный.
  - Mock Anthropic клиента (через `respx` или ручной monkeypatch) — проверь, что system prompt содержит ключевые правила, user prompt содержит evidence, парсинг JSON-ответа корректен, обработка malformed JSON (retry 1 раз → fail-soft с error в `summary`).
  - Stress: 100 evidence items в input — prompt не превышает 100k символов (truncation strategy).
  - Locale=ru: проверь, что в системном промпте действительно есть «respond in Russian».
- `packages/ai-layer/tests/test_telemetry.py`:
  - `log_ai_usage` пишет в Redis, формат корректен, expire выставлен.

## Запреты

- ❌ Реальные вызовы Anthropic в тестах (только mock).
- ❌ Реальный `ANTHROPIC_API_KEY` в репо.
- ❌ Alembic-миграции (telemetry в Redis).
- ❌ `packages/shared-models/`.
- ❌ Создание новых API-эндпоинтов (отдельный PR).

## Процесс

1. `git checkout -b feat/phase-10.1-ai-hypothesis-explanation`
2. Коммиты: `feat(ai-layer): hypothesis explanation use case`, `feat(ai-layer): cost telemetry`, `docs(adr): add ADR-0057`, `test(ai-layer): ...`.
3. `uv run pre-commit run --all-files` + `uv run pytest packages/ai-layer` перед каждым коммитом.
4. **НЕ мержить, НЕ пушить в `main`.**

## Финальный отчёт

- Ветка, коммиты, pytest, файлы, ADR-0057, оценка реальной стоимости за вызов (по модели), open questions (PII redaction policy, когда мигрируем telemetry в БД, нужна ли streaming-версия).
