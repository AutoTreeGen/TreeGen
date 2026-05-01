# Agent #3 — Phase 10.9a: parser-service API (audio sessions + consent) + arq job

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (RU comments, EN identifiers, FastAPI 0.115+,
   Pydantic v2, `uv`, pre-commit, **`--no-verify` запрещён**).
2. `docs/feature_voice_to_tree.md` — §3.1 user flow, §3.3 endpoints, §3.5
   consent gate, §3.6 retention, §3.7 acceptance.
3. `docs/adr/0064-voice-to-tree-pipeline.md` — §«Решение» (D1 worker в
   parser-service, F1 retention, G1 soft-fail).
4. `docs/adr/0049-gdpr-erasure-pipeline.md` — паттерн hard-delete на revoke.
5. `docs/adr/0036-*.md` (если есть) — permission gates `require_tree_role`.
6. Existing patterns:
   - `services/parser-service/src/parser_service/api/sharing.py` — permission
     gates (`require_tree_role(TreeRole.EDITOR)`)
   - `services/parser-service/src/parser_service/api/ai_extraction.py` —
     recent CRUD-ish API
   - `services/parser-service/src/parser_service/services/ai_source_extraction.py` —
     service-layer паттерн
   - `services/parser-service/src/parser_service/services/import_runner.py` —
     arq job паттерн
   - `services/parser-service/src/parser_service/main.py` — router register
   - `services/parser-service/src/parser_service/schemas.py` — response models
   - `services/parser-service/src/parser_service/config.py` — settings
   - `services/parser-service/tests/test_imports_api.py` — integration test
     паттерн (`AsyncClient`, MinIO fixture)

## ЗАВИСИМОСТИ — стартовать после merge

- **#1 (ORM `AudioSession`)** — мерджишь после, чтобы импортировать `AudioSession`
  из `shared_models.orm`.
- **#2 (Whisper client + `AudioTranscriber`)** — мерджишь после, чтобы
  импортировать `from ai_layer.clients.whisper import WhisperClient`
  и `from ai_layer.use_cases.transcribe_audio import AudioTranscriber`.

Если оба зависимых PR ещё в review — открой свой draft и подключайся к их
веткам через rebase, либо стартуй после их merge.

## Branch

```text
feat/phase-10-9a-api-and-job
```

От свежего main (после merge'а #1 и #2): `git checkout main && git pull
&& git checkout -b feat/phase-10-9a-api-and-job`.

## Scope

### A. API endpoints

#### A.1 Consent (`api/audio_consent.py`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/trees/{id}/audio-consent` | Прочитать состояние; 200 со `{consent_egress_at, consent_egress_provider}` или null |
| `POST` | `/trees/{id}/audio-consent` | Set `consent_egress_at = now()`; idempotent — если уже set, return 200 с current value |
| `DELETE` | `/trees/{id}/audio-consent` | Revoke + enqueue erasure job для всех `audio_sessions` дерева |

Permission gate:

- GET — `require_tree_role(TreeRole.VIEWER)`
- POST/DELETE — `require_tree_role(TreeRole.OWNER)`

(Consent — owner-only, не editor; согласовано с ADR-0036.)

Body (POST):

```python
class AudioConsentRequest(BaseModel):
    provider: Literal["openai", "self-hosted-whisper"] = "openai"
```

#### A.2 Sessions (`api/audio_sessions.py`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/trees/{id}/audio-sessions` | multipart upload (audio + mime); создаёт ORM, enqueue arq |
| `GET` | `/trees/{id}/audio-sessions` | list (paginated, default 20, max 100) |
| `GET` | `/audio-sessions/{id}` | single с transcript |
| `DELETE` | `/audio-sessions/{id}` | soft-delete (deleted_at = now()) |

Permission gate: `require_tree_role(TreeRole.EDITOR)` для POST/DELETE,
`VIEWER` для GET.

POST body — multipart, поля:

- `audio: UploadFile` (required)
- `language_hint: str | None` (optional, e.g. "ru")

Validation:

- mime_type ∈ {`audio/webm`, `audio/mpeg`, `audio/mp4`, `audio/wav`, `audio/ogg`}
- size_bytes ≤ 50 MB (cap, см. ADR-0064 §«Cost»)
- duration не валидируется на upload (известно после Whisper)

**Critical privacy gate (см. §A.3):**

> **Owner decision 2026-05-01 (Option A):** consent живёт прямо на `Tree` ORM
> в полях `audio_consent_egress_at` / `audio_consent_egress_provider` (см. #1).
> Никакого `tree_settings` или отдельного `audio_consent` ORM — читаем/пишем
> через `Tree`.

```python
async def create_audio_session(...):
    tree = await db.get(Tree, tree_id)
    if tree is None or tree.audio_consent_egress_at is None:
        raise HTTPException(
            status_code=403,
            detail={"error_code": "consent_required", "tree_id": str(tree_id)},
        )
    # ... save audio to MinIO, create ORM with snapshot:
    session.consent_egress_at = tree.audio_consent_egress_at
    session.consent_egress_provider = tree.audio_consent_egress_provider
    # ... enqueue arq job
```

#### A.3 Schemas

Дополнить `services/parser-service/src/parser_service/schemas.py`:

```python
class AudioConsentResponse(BaseModel):
    audio_consent_egress_at: datetime | None
    audio_consent_egress_provider: str | None

class AudioSessionResponse(BaseModel):
    id: UUID
    tree_id: UUID
    status: AudioSessionStatusEnum
    storage_uri: str
    mime_type: str
    duration_sec: float | None
    language: str | None
    transcript_text: str | None
    transcript_provider: str | None
    transcript_cost_usd: Decimal | None
    error_message: str | None
    created_at: datetime
    deleted_at: datetime | None

class AudioSessionListResponse(BaseModel):
    items: list[AudioSessionResponse]
    total: int
    page: int
    per_page: int
```

#### A.4 Config

`services/parser-service/src/parser_service/config.py` — добавить в `Settings`:

```python
openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
whisper_provider: str = Field(default="openai", alias="WHISPER_PROVIDER")
whisper_max_duration_sec: int = Field(default=600, alias="WHISPER_MAX_DURATION_SEC")
audio_storage_bucket: str = Field(default="audio-sessions", alias="AUDIO_STORAGE_BUCKET")
audio_max_size_bytes: int = Field(default=50_000_000, alias="AUDIO_MAX_SIZE_BYTES")
ai_dry_run: bool = Field(default=False, alias="AI_DRY_RUN")
```

`.env.example` — добавить эти ключи с комментариями.

#### A.5 Router register

`services/parser-service/src/parser_service/main.py` — `app.include_router`
для двух новых routers с правильными prefix'ами и tags.

### B. arq job — `transcribe_audio_session`

Файл: `services/parser-service/src/parser_service/jobs/transcribe_audio.py`.

```python
async def transcribe_audio_session(ctx: JobCtx, session_id: UUID) -> dict:
    """Транскрипция audio session.

    Алгоритм:
        1. Load session by id, проверить status == 'uploaded' (idempotency).
        2. Set status='transcribing'.
        3. Read audio bytes из MinIO/GCS.
        4. AudioTranscriber.run(bytes, mime_type).
        5. На success: status='ready', transcript_text=..., language=..., cost.
           На failure: status='failed', error_message=..., transcript=None.
        6. log_ai_usage в Redis.
        7. Return {session_id, status, cost_usd}.

    Retry policy: 3 попытки, экспоненциальный backoff 5s/15s/45s.
    Финальный fail → status='failed' с error category в error_message.
    """
```

`arq` config — `WorkerSettings.functions` дополнить.

### C. arq job — `erase_audio_session`

Файл: `services/parser-service/src/parser_service/jobs/erase_audio_session.py`.

Аналогично паттерну ADR-0049:

1. Load session
2. Delete object из MinIO/GCS
3. Hard-delete ORM row (DELETE, не soft)
4. Log в `gdpr_erasure_log` (если table из ADR-0049 существует)

Trigger: при `DELETE /trees/{id}/audio-consent` — enqueue этот job для каждой
неудалённой `audio_session` дерева.

### D. MinIO/GCS storage helper

Если в `services/parser-service/src/parser_service/services/` уже есть
storage abstraction (поищи `storage`, `minio`, `s3`, `gcs`) — переиспользуй.
Если нет — добавь `storage/audio_storage.py` с интерфейсом:

```python
class AudioStorage(Protocol):
    async def put(self, key: str, data: bytes, mime_type: str) -> str: ...  # returns uri
    async def get(self, uri: str) -> bytes: ...
    async def delete(self, uri: str) -> None: ...
```

С двумя реализациями (`MinIOAudioStorage`, `GCSAudioStorage`), выбираемыми
через config.

### E. Тесты — обязательные интеграционные

`services/parser-service/tests/test_audio_consent_api.py`:

- GET без consent → 200 с null fields
- POST без auth → 401
- POST с EDITOR role (не OWNER) → 403
- POST с OWNER → 201, idempotent повтор → 200 с тем же timestamp
- DELETE → enqueue erasure jobs (mock arq)

`services/parser-service/tests/test_audio_sessions_api.py`:

- **CRITICAL:** `POST /audio-sessions` без consent → **403** с
  `error_code: "consent_required"` (это CI-блокирующий тест)
- POST с consent → 201, audio попало в MinIO mock, arq job enqueued
- POST > 50 MB → 413
- POST неподдерживаемый mime_type → 415
- GET single с transcript → 200, transcript_text присутствует
- DELETE → soft-delete, GET снова показывает с deleted_at не null

`services/parser-service/tests/test_transcribe_audio_job.py`:

- Happy path: status uploaded → transcribing → ready, transcript заполнен
- Whisper 5xx → retry → success
- Whisper фатал → status=failed, error_message с category
- AI_DRY_RUN=true без api_key → mock transcript

## Definition of Done

- [ ] 7 endpoints (3 consent + 4 sessions) реализованы и зарегистрированы
- [ ] 2 arq jobs (transcribe, erase) + worker registration
- [ ] Storage abstraction для MinIO/GCS
- [ ] Все integration tests passing, **включая `consent_required` test**
- [ ] `uv run pytest services/parser-service -v` — passing
- [ ] `uv run pytest -m integration services/parser-service` — passing
  (требует `docker compose up -d`)
- [ ] `uv run mypy services/parser-service` strict
- [ ] `uv run ruff check services/parser-service && uv run ruff format --check`
- [ ] OpenAPI schema: проверь `GET /openapi.json` показывает все новые
      endpoint'ы с корректными моделями
- [ ] PR-описание ссылается на spec §3.3 + §3.5 + §3.6 (`docs/feature_voice_to_tree.md`) + ADR-0064
- [ ] PR-описание явно: «contract API стабилен для agent #4 (web UI)»

## Что НЕ трогать

- `packages/shared-models/` — закрыто agent #1
- `packages/ai-layer/` — закрыто agent #2
- `apps/web/` — зона #4
- `infrastructure/alembic/` — закрыто #1

## Подводные камни

1. **Snapshot consent на момент upload.** Не используй FK от `audio_sessions`
   в `tree_settings.audio_consent_egress_at` — копируй значение в
   `AudioSession.consent_egress_at` (см. ORM #1). Это гарантирует, что
   revoke consent не «откатывает» privacy-gate post-factum.
2. **multipart upload + arq.** UploadFile нельзя сериализовать в Redis.
   Выгружай в MinIO **перед** enqueue, передавай только `session_id` в job.
3. **Idempotency для consent POST.** Не выставлять новый timestamp при
   повторном вызове — это меняет provenance audio_sessions, привязанных к
   старому consent. Только если consent был revoked (DELETE → null), POST
   должен ставить новый timestamp.
4. **Erasure async.** DELETE consent НЕ должен ждать завершения erasure
   job'ов. Возвращай 202 Accepted с list of session_ids в очереди.
5. **AI_DRY_RUN.** Если ключа нет, **не падай** в production-mode —
   возвращай 503 с `error_code: "stt_unavailable"`. AI_DRY_RUN — только для
   dev/CI.
6. **CORS preflight для multipart.** Уже должен быть настроен в parser-service.
   Проверь, что `POST /trees/{id}/audio-sessions` проходит preflight для
   `apps/web` origin.

## Conventional Commits шаблоны

```text
feat(parser-service): add audio-consent + audio-sessions endpoints (Phase 10.9a)
feat(parser-service): add transcribe_audio_session arq job
feat(parser-service): add erase_audio_session GDPR worker
feat(parser-service): add WHISPER_* + OPENAI_* config knobs
test(parser-service): add audio sessions integration suite (consent gate critical)
```
