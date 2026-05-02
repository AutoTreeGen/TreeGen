# ADR-0075: Voice-to-tree NLU 3-pass extraction

- **Status:** Accepted
- **Date:** 2026-05-02
- **Authors:** @AutoTreeGen
- **Tags:** `ai-layer`, `parser-service`, `phase-10.9b`, `voice-to-tree`,
  `extraction`, `privacy`, `tool-use`

## Контекст

Phase 10.9a (ADR-0064) приземлила voice-to-tree audio capture +
Whisper STT: owner записывает аудио, worker конвертирует в transcript.
К моменту демо 2026-05-06 готов transcript внутри `AudioSession.transcript_text`,
но он не попадает в дерево автоматически — только как отдельный текст.

Phase 10.9b закрывает gap: вытащить из transcript'а кандидаты в **persons**,
**places**, **relationships** и **events** для последующего review (10.9c)
и (опционально) автоматического append'а в дерево (10.9d).

Силы давят на решение:

- **Cost-control.** Anthropic-вызовы биллятся per-token; runaway-cost на
  длинной session может сожрать tier-budget. Нужны pre-flight + post-pass
  caps.
- **Honest evidence.** Каждый proposal должен ссылаться на verbatim-quote
  из transcript'а — иначе review queue (10.9c) превращается в guessing
  game, а не в доказательную базу.
- **Privacy.** Anthropic — второй egress-канал после OpenAI Whisper.
  Тот же per-tree consent gate (`consent_egress_at NOT NULL`) что в 10.9a:
  если пользователь отозвал consent после записи — extraction не должен
  отправлять transcript в Anthropic. Snapshot consent'а на момент
  записи (immutable provenance) сохранён в `audio_sessions.consent_egress_at`.
- **AJ-аудитория.** Восточно-европейская еврейская генеалогия: shtetl-имена
  на иврите-в-латинице («Berdichev», «Shmuel ben Avraham»), отчества,
  Cyrillic ↔ Latin transliteration. Pipeline должен это варить нативно
  (Sonnet 4.6 справляется с EE-Jewish без extra fine-tune'а — проверено
  в Phase 15.10 multilingual name engine).
- **Reproducibility.** Owner должен видеть, ЧТО и ПОЧЕМУ модель предложила:
  `model_version`, `prompt_version`, `raw_response` — всё в БД.

## Рассмотренные варианты

### Вариант A — Single-pass structured output

Один Anthropic-вызов: «достань из transcript'а persons + places +
relationships + events, верни JSON».

- ✅ Простой контракт, один cost-cap.
- ❌ Sonnet 4.6 на 4k-input context'е «теряет» edge-events если их много;
  выпуск proposals хуже по recall.
- ❌ Single JSON-schema получается широкой и слабо-валидируемой —
  модель путает relationships c events, оба требуют persons-references.

### Вариант B — Agentic multi-turn loop

Anthropic с tool-use в loop'е: модель вызывает tool, получает
tool_result, решает следующее действие, и так далее до stop.

- ✅ Гибкий — модель сама решает порядок исследования.
- ❌ **Cost-unpredictable.** Один loop может сделать 5 вызовов вместо 3 —
  стоимость per-session непрогнозируема.
- ❌ Sloppy в нашем context'е: модель часто застревает в re-asking persons
  после relationships, эмитит дубли.
- ❌ ADR-0064 §4 явно: «Out: streaming / агентные tools — не нужны».

### Вариант C — 3-pass pipeline (выбран)

Три последовательных Anthropic-вызова: pass-1 entities, pass-2
relationships, pass-3 events. Каждый — один tool-use round (no
agentic), narrow tool-set per-pass (allowlist), результат предыдущего
pass'а передаётся как структурированный JSON в user-message следующего.

- ✅ **Predictable cost.** Ровно 3 вызова per session; pre-flight cap
  работает надёжно (3× оценка одного pass'а).
- ✅ **Ясная ответственность модели.** Pass-1 не «придумывает» события,
  pass-3 не изобретает persons.
- ✅ Per-pass allowlist — guard от lazy tool-call'ов (модель не может
  вернуть pass-1 person'у на pass-2).
- ✅ Soft-fail per-pass: если pass-2 упал — pass-1 proposals сохранены,
  status `partial_failed`. Critical для UX (long session с одной плохой
  Anthropic-flap не теряет 2 минуты пользователя).
- ❌ Pass-1 может пропустить person'у, которая упомянута только в
  context'е events (pass-3); recall < single-pass'а на edge-кейсах.
  → Mitigation: pass-3 prompt инструктирует «put unknown place in
  evidence_snippets, не emit place_index», review-UI поймает.

## Решение

3-pass pipeline (Вариант C). Implementation:

- `packages/ai-layer/src/ai_layer/use_cases/voice_to_tree_extract/`
  - `runner.py` — `VoiceExtractor.run()` orchestrator.
  - `tools.py` — 5 Anthropic tool-schemas + per-pass allowlist'ы.
  - `config.py` — cost cap константы (`VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION
    = 0.20`, `_MAX_INPUT_TOKENS_PER_PASS = 4000`, `_TOP_N_SEGMENTS = 30`).
  - `errors.py` — `VoiceExtractCostCapError` (только cost-cap бросает;
    pass-fail → `status='partial_failed'` в результате).
- `packages/ai-layer/src/ai_layer/clients/anthropic_client.py` — новый
  метод `complete_with_tools()` (Phase 10.9b — первое использование
  Anthropic tool-use в репо). Старые методы (`complete_structured`,
  `stream_completion`) **не меняются**.
- `packages/shared-models/src/shared_models/orm/voice_extracted_proposal.py` —
  ORM `VoiceExtractedProposal` + `ProposalType` / `ProposalStatus` /
  `ExtractionJobStatus` enums. Service-table (NOT TreeEntity, NOT
  SoftDeleteMixin); mirror `AudioSession` / `SourceExtraction` pattern.
- Alembic migration `0036` — additive (новая таблица + 4 индекса +
  5 CHECK constraints).
- `services/parser-service/src/parser_service/api/voice_extraction.py` —
  3 endpoints (`POST /audio-sessions/{id}/extract`, `GET extractions
  list`, `GET extractions/{job_id}`).
- `services/parser-service/src/parser_service/jobs/voice_extract.py` —
  arq worker job; `voice_extract_job` зарегистрирован в
  `WorkerSettings.functions`.

### Cost cap

Pre-flight (до Anthropic-вызова): `3 × estimate_input_tokens(transcript_chars)`
× pricing → `VoiceExtractCostCapError` если > `MAX_TOTAL_USD_PER_SESSION`.

Post-pass (после каждого pass'а): cumulative cost > cap → abort оставшиеся
pass'ы, status `cost_capped`, сохраняем proposals из выполненных pass'ов.

Дефолт `0.20 USD` per session покрывает 3 × ~$0.05 (Sonnet 4.6, 4k
input + 1k output) с 30% запасом. Tier-based override планируется в
12.x (Stripe subscription tier → max_total_usd).

### Privacy

Anthropic — второй egress-канал. POST `/extract` 403 `consent_required`
если `audio_sessions.consent_egress_at IS NULL`. Это duplicate-of-effort
с UI и DB-уровнем (`consent_egress_at NOT NULL`), но defence-in-depth
обязателен — Phase 10.9.x может добавить self-hosted Whisper, и тогда
egress-status станет nullable.

POST `/extract` 409 `transcript_not_ready` если `status != 'ready'` или
`transcript_text IS NULL` — пытаемся уважать lifecycle, не запускать
extraction на пустых данных.

### Failure handling

Per-pass: один retry на pass (ADR-0064 §G1 пattern); если оба attempt'а
фейлятся — status `partial_failed`, error_message пишется в результат,
proposals из предыдущих pass'ов сохраняются.

Pre-flight cap exceeded — `VoiceExtractCostCapError` ДО Anthropic-вызова;
worker не сохраняет ничего, log warn, возвращает `status="cost_capped"`
из job'а. UI получит пустой group через `GET extractions list`.

Worker-level: arq retry-budget стандартный (см. `WorkerSettings.functions`).
`voice_extract_job` сам soft-fail'ится — большинство Anthropic-ошибок
не доходят до arq retry'я.

## Что НЕ закрыто

- **Tree assembly** (создание `Person` / `Family` / `Event` ORM rows
  на основе approved proposals) — Phase **10.9c-cold** (отдельный agent /
  ADR). Здесь только эмитим proposals.
- **Review queue UI** — Phase 10.9c (web).
- **Re-extract после edit'а transcript'а** — Phase 10.9d. POST `/extract`
  принимает `force=true`, но в этом PR force игнорируется (idempotent).
- **Name engine integration** (Phase 15.10) — proposals остаются raw;
  review queue или append-mode (10.9d) дедуплицирует по фонетике.
- **Streaming / agentic loop** — out of scope per ADR-0064 §4.
- **Self-hosted Whisper / faster-whisper** — Phase 10.9.x (privacy-tier).
  Этот пайплайн работает поверх готового OpenAI Whisper output'а.

## Когда пересмотреть

- WER на mixed_ru_he fixture > 30% (модель teряет AJ-имена / shtetl).
  Возможно: дополнительный Phase 15.10 name engine pass.
- Cost > $0.30/session на 90-percentile в beta — переход на cheaper
  model (Haiku-tier) для pass-2/3.
- Recall events < 60% — A/B prompt-revision (`voice_extract_pass3_v2`).
- Если 10.9c показывает >50% rejected proposals — pre-filter каскад
  (entity-resolution NameMatcher до того как эмитим в queue).

## Ссылки

- ADR-0043 — ai-layer architecture (Anthropic provider).
- ADR-0049 — GDPR erasure pipeline (CASCADE pattern).
- ADR-0057 — AI hypothesis explanation pattern (prompt registry,
  Redis telemetry, `AI_DRY_RUN`).
- ADR-0064 — voice-to-tree pipeline (Phase 10.9a; Whisper, consent gate,
  cost cap).
- ADR-0068 — multilingual name engine (15.10; future integration).
- ADR-0071 — evidence weight + provenance split (Phase 22.5;
  proposal-to-evidence mapping в 10.9c).
