# Agent #2 — Phase 10.9a: ai-layer Whisper client + transcribe use case + pricing

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (RU comments, EN identifiers, Conventional Commits,
   Python 3.13, Pydantic v2, `uv`, pre-commit must pass, **`--no-verify` запрещён**).
2. `docs/feature_voice_to_tree.md` — §3.4 «Whisper-интеграция».
3. `docs/adr/0064-voice-to-tree-pipeline.md` — §«Решение» (A1 OpenAI Whisper, G1 soft-fail).
4. `docs/adr/0043-ai-layer-architecture.md` — общая архитектура слоя.
5. `docs/adr/0057-ai-hypothesis-explanation.md` — паттерны `AI_DRY_RUN`,
   Redis-телеметрии, soft-fail, тестов с моками.
6. Existing patterns:
   - `packages/ai-layer/src/ai_layer/use_cases/explain_hypothesis.py` — структура use_case
   - `packages/ai-layer/src/ai_layer/use_cases/source_extraction.py` — recent example
   - `packages/ai-layer/src/ai_layer/pricing.py` — табличка pricing + estimate-helpers
   - `packages/ai-layer/src/ai_layer/telemetry.py` — `log_ai_usage` (re-use)
   - `packages/ai-layer/src/ai_layer/clients/` — паттерн клиентов (Anthropic, Voyage)
   - `packages/ai-layer/pyproject.toml` — для добавления зависимости
   - `packages/ai-layer/tests/test_pricing.py` — паттерн тестов pricing

## Задача

Реализовать STT-клиент + transcribe use case + pricing extension в
`packages/ai-layer/`. Pure-package, без I/O в БД (БД-mutation на стороне
agent #3).

## Branch

```text
feat/phase-10-9a-ai-layer-whisper
```

От свежего main: `git checkout main && git pull && git checkout -b feat/phase-10-9a-ai-layer-whisper`.

## Scope

### A. Зависимость `openai` SDK

`packages/ai-layer/pyproject.toml` — добавить в `dependencies`:

```toml
"openai>=1.0",
```

(в алфавитный порядок).

`uv lock` — обновить локально, проверь, что разрешается.

### B. Whisper-клиент

Файл: `packages/ai-layer/src/ai_layer/clients/whisper.py`.

```python
class TranscriptResult(BaseModel):
    """Pydantic-результат расшифровки."""
    text: str
    language: str | None              # 'ru', 'en', 'he', ...
    duration_sec: float | None
    model: str                         # e.g. 'whisper-1'
    cost_usd: Decimal                  # рассчитано через pricing

class WhisperClient:
    """Тонкая обёртка вокруг OpenAI Whisper API.

    Args:
        api_key: OPENAI_API_KEY; если None и AI_DRY_RUN=true — мок.
        max_duration_sec: cap (default 600 — см. ADR-0064).
    """

    def __init__(self, api_key: str | None = None, max_duration_sec: int = 600): ...

    def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language_hint: str | None = None,
    ) -> TranscriptResult: ...
```

Требования:

- **AI_DRY_RUN behaviour:** если `api_key is None` (env var не выставлен) И
  `AI_DRY_RUN=true` — возвращай mock `TranscriptResult(text="[dry-run mock RU]", ...)`.
  Если `api_key is None` и `AI_DRY_RUN!=true` — `raise WhisperConfigError`.
- **Duration cap:** **post-response** check — после ответа Whisper, если
  `result.duration_sec > max_duration_sec` → `raise AudioTooLongError`
  (мы уже заплатили за вызов, но защищаем downstream от дикого транскрипта).
  Pre-flight защита — **size cap** на стороне #3 (`audio_max_size_bytes=50MB`,
  config), достаточно для 600 сек Opus@32kbps. Не добавляй `mutagen` или
  другую duration-извлекалку — лишняя зависимость для marginal value.
- **Soft-fail:** на 5xx или timeout — **один retry** с экспоненциальным
  backoff (паттерн из ADR-0057 §F). После двух неудач — `raise WhisperApiError`.
  ValidationError на структуре ответа — тоже один retry, потом hard.
- **Cost computation:** через `ai_layer.pricing.estimate_whisper_cost_usd(duration_sec)`.

Exception-классы: `WhisperConfigError`, `AudioTooLongError`, `WhisperApiError`,
все наследуются от общего `WhisperError(Exception)`.

### C. Use case `AudioTranscriber`

Файл: `packages/ai-layer/src/ai_layer/use_cases/transcribe_audio.py`.

```python
@dataclass
class TranscribeAudioInput:
    audio_bytes: bytes
    mime_type: str
    language_hint: str | None = None

@dataclass
class TranscribeAudioOutput:
    transcript: str
    language: str | None
    duration_sec: float | None
    provider: str                      # 'openai-whisper-1'
    model_version: str
    cost_usd: Decimal
    error: str | None = None           # set on soft-fail

class AudioTranscriber:
    def __init__(self, client: WhisperClient): ...

    def run(self, input_: TranscribeAudioInput) -> TranscribeAudioOutput:
        """Транскрипция + телеметрия + soft-fail.

        Возвращает ``TranscribeAudioOutput`` всегда (с error на failure).
        Caller (agent #3 worker) маппит на `AudioSession.status`.
        """
```

**Telemetry:** после успешной (или failed) транскрипции — `log_ai_usage(
use_case='transcribe_audio', model='whisper-1', input_tokens=0,
output_tokens=0, audio_duration_sec=duration_sec, cost_usd=cost_usd)`.

Поле `audio_duration_sec` нужно ДОБАВИТЬ в payload `log_ai_usage` —
backward-compatible (опциональный kwarg). Если требует изменения схемы
Redis-list — окей, но **не вводи новых ORM-таблиц**.

### D. Pricing extension

`packages/ai-layer/src/ai_layer/pricing.py` — добавить:

```python
WHISPER_PRICING_PER_MIN_USD: dict[str, Decimal] = {
    "whisper-1": Decimal("0.006"),
}

def estimate_whisper_cost_usd(
    duration_sec: float,
    model: str = "whisper-1",
) -> Decimal:
    """Оценка стоимости транскрипции (округление до 6 знаков)."""
```

Тесты в `tests/test_pricing.py`:

- `estimate_whisper_cost_usd(60)` == `Decimal("0.006")`
- `estimate_whisper_cost_usd(30)` == `Decimal("0.003")`
- Unknown model → `KeyError` или `ValueError` (выбери — match существующему
  `estimate_*` паттерну).

### E. Тесты

`packages/ai-layer/tests/test_whisper_client.py`:

- `transcribe()` с моком OpenAI SDK → корректный `TranscriptResult`
- `transcribe()` с 5xx → один retry → success на втором → success
- `transcribe()` с 5xx → один retry → 5xx → `WhisperApiError`
- `transcribe()` с `AI_DRY_RUN=true` без api_key → mock-payload
- `transcribe()` без api_key и без dry-run → `WhisperConfigError`
- Whisper-ответ с `duration > 600 sec` → `AudioTooLongError` (post-response)

`packages/ai-layer/tests/test_transcribe_audio.py`:

- `AudioTranscriber.run()` happy-path → `TranscribeAudioOutput.error is None`
- `AudioTranscriber.run()` с soft-fail → `error` populated, transcript empty
- Telemetry called once с правильным `use_case` и `audio_duration_sec`
- Caller получает `cost_usd` Decimal с rounding 6 places

## Definition of Done

- [ ] `openai>=1.0` в pyproject.toml + `uv.lock` обновлён
- [ ] `WhisperClient` + `TranscriptResult` + exception classes
- [ ] `AudioTranscriber` use case
- [ ] `pricing.py` extended + tests
- [ ] `telemetry.py` принимает `audio_duration_sec` (backward-compatible)
- [ ] Все тесты passing: `uv run pytest packages/ai-layer -v`
- [ ] `uv run mypy packages/ai-layer` strict — без `# type: ignore` без
      обоснования
- [ ] `uv run ruff check packages/ai-layer && uv run ruff format --check packages/ai-layer`
- [ ] `uv run pre-commit run --files <ваши_файлы>` — passing
- [ ] PR-описание ссылается на ADR-0064 §3.4 + ADR-0057 (паттерн)
- [ ] PR-описание явно: «contract `WhisperClient.transcribe()` + `AudioTranscriber.run()`
      стабилен; agent #3 импортирует из `ai_layer.clients.whisper` и
      `ai_layer.use_cases.transcribe_audio`»

## Что НЕ трогать

- `packages/shared-models/` — зона #1 (если нужен `AudioSession` тип —
  **не импортируй** в ai-layer; ai-layer не знает про ORM)
- `services/parser-service/` — зона #3
- `apps/web/` — зона #4

## Подводные камни

1. **OpenAI SDK API на whisper-1** — endpoint `audio.transcriptions.create()`,
   принимает file-like object. Адаптируй `audio_bytes` через `io.BytesIO`.
2. **Mime detection.** OpenAI ожидает `file=(filename, file, mimetype)`-tuple.
   Filename влияет на инференс расширения. Используй `f"audio.{ext}"`
   где `ext` маппится из `mime_type`.
3. **Rate limits OpenAI.** Whisper API: 50 RPM на free tier, 500 RPM на pay-as-you-go.
   Для бета-owner'а с 60 мин/день — не упрёмся, но добавь `RateLimitError`
   обработку через тот же retry-pattern.
4. **Voice biometric concern.** Не добавляй features extraction вне Whisper API.
   Чисто text-out, никакого `embedding`/voiceprint.
5. **Decimal precision.** Cost — Decimal, не float. Везде. Иначе тесты на
   `pricing` посыплются.

## Conventional Commits шаблоны

```text
feat(ai-layer): add WhisperClient + AudioTranscriber use case (Phase 10.9a)
feat(ai-layer): extend pricing.py with whisper-1 ($0.006/min)
chore(ai-layer): add openai>=1.0 dependency
test(ai-layer): add Whisper client + transcribe audio tests
```
