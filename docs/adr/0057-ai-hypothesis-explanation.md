# ADR-0057: AI hypothesis explanation as the first production AI use case (Phase 10.1)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `ai`, `llm`, `hypothesis`, `phase-10.1`, `privacy`

## Контекст

Phase 10.0 (ADR-0043) посадил skeleton `packages/ai-layer/`: клиенты
Anthropic / Voyage, prompt registry, заглушка `HypothesisSuggester`. Ни
одного **продакшн** use case'а из этого skeleton'а ещё не выкатывалось:
LLM пока не вызывается из реальных code-paths.

Phase 4.9 (review queue) показывает пользователю гипотезы вида
«А и Б — возможно один и тот же человек», вычисленные детерминированным
inference-engine. Голый список evidence-rows тяжело интерпретировать
без понимания внутренней семантики rule'ов: `rule.dna.shared_cm > 1300`
ничего не говорит обычному исследователю. Owner проекта хочет короткое
естественно-языковое объяснение «почему», построенное на evidence-graph
гипотезы.

Это **низкорисковый кейс**, идеальный для первого продакшн вызова LLM:

- **Read-only:** explainer ничего не пишет в БД и не меняет hypothesis
  state. Ошибка LLM = плохой текст, не corrupted данные.
- **Evidence-grounded:** prompt получает уже посчитанные evidence-items;
  «грунтовать» нечего, кроме text-объяснения. Hard rules в системном
  промпте запрещают придумывать факты.
- **Read-after-write workflow:** gate перед вызовом — owner кликает
  «explain» в UI, не background job. Это даёт rate-limiting естественным
  образом и делает биллинг предсказуемым.
- **Bilingual UI:** Phase 4.13b уже катит i18n на app — explainer должен
  поддерживать `en` и `ru`.

Силы давления:

- **Cost.** Cap на typical request: ~3000 input + ~500 output tokens.
  При Sonnet 4.6 pricing ($3/MTok in, $15/MTok out) это ≈ $0.0165 за
  вызов. Owner поставил target < $0.01 — мы его **не достигаем** на
  базовой модели; см. §Решение про оптимизации.
- **Privacy.** Имена, даты, места живых людей — PII по GDPR Art. 6.
  Anthropic logs запросы 30 дней (Standard tier), 0 дней с
  Zero-Data-Retention enterprise add-on. У нас Standard. Передавать
  ДНК-сегменты строго запрещено (Art. 9 special category — ADR-0043
  §«Privacy»).
- **Determinism.** Объяснение не должно «галлюцинировать» имена /
  даты, которых нет в evidence. Хард-правила в system prompt + caller
  валидирует, что в `summary` нет токенов, отсутствующих во входе
  (отложено до Phase 10.x — пока полагаемся на disciplined prompt).
- **Cost telemetry.** Без учёта расходов мы не сможем поставить
  budget alerts. Phase 10.5 (биллинг для пользователей) потребует БД
  таблицы, но она пока не нужна — на 10.1 хватит лога в Redis.

## Рассмотренные варианты

### A. Что генерирует LLM

- **JSON-структурированный объяснитель (выбрано):** LLM возвращает
  Pydantic-валидируемый `{summary, key_evidence, caveats, confidence_label}`.
  - ✅ UI (Phase 4.9) рендерит секции независимо — может скрывать
    `caveats` collapse-блоком, может цветить `confidence_label`.
  - ✅ Тестируемо: можем мокать ответ и валидировать схему.
  - ✅ Совместимо с будущим streaming (Phase 10.x) — secondary поля
    приходят последними.
  - ❌ Чуть длиннее prompt (нужна schema-инструкция). Acceptable.
- **Plain-text rationale:** короче prompt, но нельзя структурировать
  UI-вывод; не выбран.
- **HTML-фрагмент:** tightly couples LLM с UI-фреймворком; отвергнуто.

### B. Локализация (en / ru) — где переключатель

- **System-prompt directive (выбрано):** Jinja2-условие
  `{% if locale == "ru" %}respond in Russian{% else %}respond in English{% endif %}`
  переключает язык ответа. Schema (JSON-ключи) остаётся английской.
  - ✅ Один шаблон — один маршрут поддержки. A/B и регрессии
    видны в одном файле.
  - ✅ LLM (Sonnet 4.6) одинаково хорошо рассуждает на ru и en.
- **Два разных шаблона `_v1_en.md` / `_v1_ru.md`:** дублирование hard
  rules → drift. Отвергнуто.
- **Post-translate:** второй LLM-вызов удваивает стоимость. Отвергнуто.

### C. Storage cost telemetry — Redis vs ORM таблица

- **Redis-list `ai_usage:log` + 30-day expire (выбрано):**
  - ✅ Ноль миграций. Phase 10.1 — pure addition, не лежит на
    критическом пути CI с прочими feature-PR.
  - ✅ Append-only LPUSH дешёвый.
  - ✅ Tooling (Redis) уже есть в `parser-service` / `telegram-bot`.
  - ❌ Нет агрегатов по user / model / use_case (нужно вытаскивать в
    BigQuery вручную). Acceptable до 10.5: внутренний аудит, ноль
    SLO-нагрузки.
- **ORM таблица `ai_usage_events` сейчас:** alembic-миграция +
  shared-models entry + schema_invariants + ORM allowlist
  (см. memory: `feedback_orm_allowlist.md`) → 4 файла, 2 PR-ревью,
  2 недели задержки. Phase 10.1 этого не оправдывает.
- **Stdout-logging без Redis:** теряется при ротации; для биллинг-аудита
  слишком эфемерно.

### D. Dry-run mode — для локалки без ANTHROPIC_API_KEY

- **`AI_DRY_RUN=true` env-flag → mock-payload (выбрано):**
  - ✅ Локальный dev может тестировать UI без секрета.
  - ✅ В CI explicit-fallback (если кто-то забудет mock в тесте — env'а нет).
  - ✅ Mock localized: `summary` на ru/en. Caller-side можно показывать
    разработчику честный «это моки».
- **Conditional skip:** UI рисует «no explanation» при отсутствии ключа.
  Отвергнуто: разработчик не сможет тестировать explainer-секцию.
- **Только в тестах через monkeypatch:** не покрывает `pnpm dev` workflow.

### E. Privacy — какие поля evidence уходят в Anthropic

- **Передаём evidence as-is (выбрано на этой итерации, с TODO):**
  - ✅ Реализационно простейший путь.
  - ❌ Имена и места попадают в Anthropic logs (30-day retention, Standard
    tier). Это допустимо для **бета-тестеров** (явное consent на
    AI-features в settings — Phase 4.10b), но не для public-tree share
    (ADR-0047).
- **Redaction layer перед вызовом (отложено в Phase 10.x):**
  - Заменять surnames на хэш-токены, dates округлять до десятилетий,
    места — до уровня страны. LLM-объяснение деградирует, но PII
    минимизируется.
  - Требует стороннего юридического review «что есть PII в нашем
    domain'е» — отдельная задача.
- **DNA-сегменты НЕ передаются никогда (жёсткое правило):** на этой
  итерации это enforced caller-уровнем (parser-service не передаёт DNA-cM
  в `EvidenceItem.details` без явного opt-in пользователя). Schema
  `EvidenceItem` принимает строку — caller отвечает, что внутри.

### F. Soft-fail vs hard-fail при malformed JSON

- **One retry → fail-soft `HypothesisExplanation` с error summary
  (выбрано):**
  - ✅ UI Phase 4.9 продолжает показывать гипотезу, даже если LLM глючит.
    Объяснение — UX-bonus, не функциональная зависимость.
  - ✅ Retry за нас делает SDK (rate-limit / 5xx); ValidationError —
    наш собственный retry, перед fail-soft.
- **Hard-fail (бросить exception):** UI должен ловить и рендерить
  «нет объяснения» — больше boilerplate в caller'ах.

## Решение

Выбраны: **A — структурированный JSON**, **B — Jinja2 condition в
system-prompt**, **C — Redis-list (Phase 10.5 миграция в БД)**,
**D — `AI_DRY_RUN` env**, **E — пока без redaction (TODO)**,
**F — soft-fail после одного retry**.

Реализация:

- `packages/ai-layer/src/ai_layer/use_cases/explain_hypothesis.py` —
  `HypothesisExplainer.explain(hypothesis, locale) -> HypothesisExplanation`.
- `packages/ai-layer/src/ai_layer/prompts/hypothesis_explanation_v1.md` —
  Jinja2-шаблон с few-shot 2 примера и language directive.
- `packages/ai-layer/src/ai_layer/pricing.py` — pricing-таблица
  Anthropic 2026-04-30 + `estimate_cost_usd`.
- `packages/ai-layer/src/ai_layer/telemetry.py` — `log_ai_usage` →
  Redis LPUSH + EXPIRE.
- HTTP-эндпоинт `POST /hypotheses/{id}/explain` — отдельный мини-PR
  после merge'а этой ветки (см. ROADMAP §14.1).

## Последствия

### Положительные

- Первый production AI use case закрыт без изменений в shared-models
  / inference-engine: ноль межсервисных breaking-changes.
- `ai-layer` теперь даёт downstream-фазам три рабочих компонента,
  pricing-таблицу и telemetry-гард — Phase 10.2+ строится без
  переисполнения foundation.
- UI Phase 4.9 получает интеграционный hook без повторного открытия
  inference-engine code-paths.

### Отрицательные / стоимость

- **Не достигаем cost-target $0.01 за вызов на baseline-модели.**
  Snapshot 2026-04-30, Sonnet 4.6: 3000 input × $3/MTok + 500 output ×
  $15/MTok = **$0.0165**. Мы это сознательно принимаем на 10.1; пути
  оптимизации:
  1. Prompt caching (5-min TTL Anthropic) — повтор тех же hard-rules
     сократит input tokens на ~40% при cache-hit. Phase 10.x.
  2. Truncate evidence до top-N (реализовано: `MAX_EVIDENCE_ITEMS=50`,
     stable-sort по confidence DESC).
  3. Haiku 4.5 для low-confidence гипотез (≤ 0.5 composite_score) —
     $1/MTok in, $5/MTok out → $0.0055/вызов. Phase 10.5 (роутер по
     confidence-tier).
  4. Batch API (-50%) — для офлайн-генерации explanation-cache. Phase 10.x.

  Бюджет на 10.1 (бета): owner оценивает ≤ 100 ручных explain-кликов в
  день → **≤ $1.65/day** (~$50/month) — приемлемо.
- **Privacy debt:** имена / места / даты улетают в Anthropic. Beta-only;
  необходимо включить explicit consent toggle в Phase 4.10b settings до
  публичного rollout'а.
- **Telemetry в Redis — без агрегатов:** аудит требует ручного
  `LRANGE ai_usage:log 0 -1 | jq …`.

### Риски

- LLM может галлюцинировать имена, не присутствующие в evidence
  (low risk на Sonnet 4.6 + hard rules в prompt). Митигация Phase 10.x:
  валидатор, проверяющий, что каждое слово из `summary` встречается в
  evidence или subjects.
- Stripe-биллинг (Phase 12.0) ещё не учитывает AI-расходы — overage
  пока ложится на проект. Acceptable до 10.5.

### Что нужно сделать в коде

- Эта фаза: реализовано в `packages/ai-layer/`.
- Phase 10.1b: HTTP-эндпоинт в `parser-service` или `inference-service`
  (отдельный PR) + клиент в `apps/web/`.
- Phase 10.5: ORM-модель `ai_usage_events` + alembic-миграция +
  агрегаты + UI в settings.

## Когда пересмотреть

- Стоимость > $200/month → переключиться на Haiku-роутер или внедрить
  prompt caching (см. §«Отрицательные»).
- Жалоба от GDPR-аудита по поводу PII-leakage в LLM logs → срочно
  внедрить redaction layer.
- Запрос на streaming в UI (TTFB > 3 сек на текущей реализации) →
  переключиться на streaming через `messages.stream`, переехать
  с `complete_structured` на сервер-side aggregation.
- Появление вендор-локального LLM (Anthropic on-prem, Llama 4 70B локально),
  делающего privacy-вопрос мooot → пересмотреть E.

## Ссылки

- Связанные ADR: ADR-0043 (AI layer architecture, Phase 10.0),
  ADR-0016 (inference-engine core types — `Evidence`/`Hypothesis`),
  ADR-0047 (public-tree share privacy model — gates на share-time),
  ADR-0012 (consent policy — отложенная Phase 4.x референция).
- ROADMAP §14.1 — Phase 10.1 use cases.
- Anthropic pricing: <https://www.anthropic.com/pricing> (snapshot 2026-04-30).
- Anthropic data retention: <https://docs.anthropic.com/en/docs/legal-center/data-retention>
