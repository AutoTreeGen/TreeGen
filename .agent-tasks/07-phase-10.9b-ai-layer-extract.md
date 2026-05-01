# Agent #7 — Phase 10.9b: ai-layer NLU extraction (transcript → ProposalSet)

> **Trigger: post-demo, после 2026-05-06.** Не стартуй до тех пор, пока
> 10.9a (агенты #1–#4 + staging-prep #6) не приземлится в `main` и
> демо инвестору не пройдёт. Если 10.9a откатился или сдвинулся — этот
> бриф автоматически сдвигается; не лезь вперёд.

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## TASK

Реализовать NLU extraction layer над готовым `AudioSession.transcript_text`:
3-pass-pipeline (entities → relationships → temporal-spatial), который
эмитит `voice_extracted_proposals` records с per-item `confidence` и
`evidence_snippets`. Никакого write'а в `persons` / `families` / `events`
напрямую — только proposals (review queue в 10.9c превращает их в
`Hypothesis`). Pure post-demo work; demo критпуть закрыт в 10.9a.

## CONTEXT — обязательно прочитать перед стартом

1. `CLAUDE.md` — конвенции (RU comments, EN identifiers, Conventional
   Commits, Python 3.13, Pydantic v2, `uv`, pre-commit must pass,
   **`--no-verify` запрещён**).
2. `docs/adr/0064-voice-to-tree-pipeline.md` — §3.6 (privacy / data
   retention; те же rules здесь — egress только при consent), §«Что нужно
   сделать в коде» 10.9b. Cost cap для transcribe_audio здесь
   расширяется на extract_voice (ниже §DATA MODEL → cost cap).
3. `docs/feature_voice_to_tree.md` — §4 «Phase 10.9b — outline» (top-N
   сегментов + 4k token truncation, no streaming, no agentic tools).
4. **PR #164 (10.9a ORM, agent #1)** — финальный shape `AudioSession`,
   особенно `transcript_text`, `language`, `tree_id`, `consent_egress_at`.
   Не предполагай поля от руки — читай merged-state ORM.
5. ADR-0057 (`docs/adr/0057-ai-hypothesis-explanation.md`) — паттерн
   prompt-registry, Redis-телеметрии, `AI_DRY_RUN`, soft-fail.
6. ADR-0043 (`docs/adr/0043-ai-layer-architecture.md`) — общая архитектура
   слоя (provider-agnostic, Anthropic-only для NLU здесь).
7. Existing patterns:
   - `packages/ai-layer/src/ai_layer/clients/anthropic_client.py` —
     Anthropic API-клиент; tool-use здесь будет первое использование в
     ai-layer, расширь клиент аккуратно (новый method, не правь
     существующие).
   - `packages/ai-layer/src/ai_layer/use_cases/source_extraction.py` —
     ближайший analogue (extraction → structured proposals).
   - `packages/ai-layer/src/ai_layer/use_cases/explain_hypothesis.py` —
     паттерн prompt-registry + telemetry.
   - `packages/ai-layer/src/ai_layer/pricing.py` — добавить cost-helper
     для Anthropic NLU calls (input + output tokens, не per-min как
     Whisper).
   - `packages/shared-models/src/shared_models/orm/audio_session.py` —
     reference для миксинов (SoftDelete, Provenance) при создании
     `voice_extracted_proposals` ORM.

## WORKTREE

```text
F:/Projects/TreeGen-wt/phase-10-9b-ai-layer-extract
```

```bash
git fetch origin main
git worktree add F:/Projects/TreeGen-wt/phase-10-9b-ai-layer-extract -b feat/phase-10-9b-ai-layer-extract origin/main
cd F:/Projects/TreeGen-wt/phase-10-9b-ai-layer-extract
uv sync --all-extras --all-packages
pnpm install   # на случай, если правишь что-то рядом — но scope этого PR'а UI не трогает
```

## GOAL — 3-pass NLU pipeline

Один transcript → три последовательных вызова Anthropic, каждый со своим
tool-set'ом. Между pass'ами накапливаем context:

| Pass | Input | Tools available | Output |
|---|---|---|---|
| 1 — **entities** | transcript chunks (top-N) | `create_person`, `add_place`, `flag_uncertain` | candidate persons + places (без связей) |
| 2 — **relationships** | persons[] из pass 1 + transcript | `link_relationship`, `flag_uncertain` | edges parent_of / spouse_of / sibling_of / witness_of |
| 3 — **temporal-spatial** | persons[]+edges+places из pass 1+2 + transcript | `add_event`, `flag_uncertain` | birth/death/marriage/migration events с date_start/date_end + place_id |

Каждый pass — отдельный Anthropic call с своим system-prompt'ом и
narrow tool-set'ом. **Один pass — один tool-use round** (no agentic
multi-turn loop; ADR-0064 §4 явно: «Out: streaming / агентные tools —
не нужны»). Если модель попыталась вызвать инструмент не из своего
pass'а — игнорировать (логировать как `unexpected_tool` в телеметрии).

**Аккумуляция:** результат pass N — input context для pass N+1.
Передаём как структурированный JSON в user-message, не как новые tool
results. Это критично — иначе модель «доводит» свои pass-1 догадки
вместо того чтобы делать pass-2 заново на свежую голову.

**Failure handling (паттерн ADR-0057 §F + ADR-0064 §G):** один retry
на каждый pass, exponential backoff. Если после retry pass провалился —
сохраняем все proposals из ПРЕДЫДУЩИХ pass'ов + помечаем
`extraction_status = "partial_failed"` и пишем error category в
`error_message`. Клиент (10.9c review queue) увидит частичный set
proposals + явный warning.

## DATA MODEL

### Новая ORM таблица `voice_extracted_proposals`

```python
# packages/shared-models/src/shared_models/orm/voice_extracted_proposal.py
class VoiceExtractedProposal(Base, SoftDeleteMixin, ProvenanceMixin):
    __tablename__ = "voice_extracted_proposals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tree_id: Mapped[UUID] = mapped_column(ForeignKey("trees.id"), index=True)
    audio_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("audio_sessions.id"), index=True
    )

    # set group: один extraction-job = N proposals; объединяем по
    # `extraction_job_id` (UUID, не FK; identity для review-queue group-by).
    extraction_job_id: Mapped[UUID] = mapped_column(index=True)

    proposal_type: Mapped[ProposalType]   # enum: person | place | event | relationship
    payload: Mapped[dict] = mapped_column(JSONB)  # tool input args, валидируется по schema
    confidence: Mapped[Decimal]           # 0.00–1.00 (как Whisper duration — Decimal, не float)
    evidence_snippets: Mapped[list[str]] = mapped_column(JSONB)  # цитаты из transcript (≥1)

    # raw_response pattern (mirrors source_extraction):
    raw_response: Mapped[dict] = mapped_column(JSONB)  # полный Anthropic response для аудита
    pass_number: Mapped[int]              # 1 | 2 | 3 — какой pass сгенерил
    status: Mapped[ProposalStatus]        # pending | approved | rejected (для 10.9c)

    # cost telemetry (ADR-0057 паттерн)
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cost_usd: Mapped[Decimal]
    model_version: Mapped[str]            # 'claude-sonnet-4-6' и т.п.
    prompt_version: Mapped[str]           # 'voice_extract_pass1_v1'

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None]
```

`ExtractionJob` — отдельный lightweight ORM или просто UUID-группер?
**UUID-группер** (нет таблицы `voice_extraction_jobs`); group-by
`extraction_job_id` достаточно для review queue в 10.9c. Если
понадобится progress tracking — вынесем в отдельный ADR.

Alembic — одна миграция, additive (новая таблица + enum'ы).
`SERVICE_TABLES` allowlist в `tests/test_schema_invariants.py` —
**обязательно** добавить `voice_extracted_proposals` в этом же PR.

### Cost cap per session

Расширяем cost-cap pattern из ADR-0064 §3.6 / §«Cost»:

| Cap | Default | Rationale |
|---|---|---|
| `VOICE_EXTRACT_MAX_INPUT_TOKENS_PER_PASS` | 4_000 | Per ADR-0064 §4 outline (top-N + truncate). |
| `VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION` | `Decimal("0.20")` | 3 passes × ~5–8c каждый — sane cap. |
| `VOICE_EXTRACT_TOP_N_SEGMENTS` | 30 | Если transcript разбит на >30 сегментов (Whisper не делает; здесь руками — split по `\n\n`). |

Cap превышен на pre-flight — `raise VoiceExtractCostCapError` до Anthropic
вызова. Cap превышен после — abort оставшиеся pass'ы, сохраняем то что
есть, status `cost_capped`.

## TOOL SCHEMA — Anthropic tool-use definitions

Все 5 tools определяются в одном месте —
`packages/ai-layer/src/ai_layer/use_cases/voice_to_tree_extract/tools.py` —
и шарятся между pass'ами с явным allowlist'ом per-pass. Точные
JSON-schemas:

### `create_person` (pass 1)

```json
{
  "name": "create_person",
  "description": "Predict a person mentioned in the transcript. Each fact must be backed by an evidence_snippet.",
  "input_schema": {
    "type": "object",
    "properties": {
      "given_name": {"type": "string"},
      "surname": {"type": "string"},
      "patronymic": {"type": "string"},
      "sex": {"type": "string", "enum": ["M", "F", "U"]},
      "birth_year_estimate": {"type": "integer", "minimum": 1500, "maximum": 2100},
      "death_year_estimate": {"type": "integer", "minimum": 1500, "maximum": 2100},
      "is_alive": {"type": "boolean"},
      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
      "evidence_snippets": {"type": "array", "items": {"type": "string"}, "minItems": 1}
    },
    "required": ["confidence", "evidence_snippets"]
  }
}
```

### `add_place` (pass 1)

```json
{
  "name": "add_place",
  "description": "Predict a place (city, shtetl, country) mentioned. Use the most-specific level you can support with the snippet.",
  "input_schema": {
    "type": "object",
    "properties": {
      "name_raw": {"type": "string"},
      "place_type": {"type": "string", "enum": ["city", "town", "shtetl", "region", "country", "unknown"]},
      "country_hint": {"type": "string"},
      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
      "evidence_snippets": {"type": "array", "items": {"type": "string"}, "minItems": 1}
    },
    "required": ["name_raw", "confidence", "evidence_snippets"]
  }
}
```

### `link_relationship` (pass 2)

```json
{
  "name": "link_relationship",
  "description": "Link two persons from pass 1. Refer by `subject_index` / `object_index` (1-based, into pass-1 persons array provided in user message).",
  "input_schema": {
    "type": "object",
    "properties": {
      "subject_index": {"type": "integer", "minimum": 1},
      "object_index": {"type": "integer", "minimum": 1},
      "relation": {"type": "string", "enum": ["parent_of", "spouse_of", "sibling_of", "witness_of"]},
      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
      "evidence_snippets": {"type": "array", "items": {"type": "string"}, "minItems": 1}
    },
    "required": ["subject_index", "object_index", "relation", "confidence", "evidence_snippets"]
  }
}
```

### `add_event` (pass 3)

```json
{
  "name": "add_event",
  "description": "Anchor a temporal-spatial event to a person from pass 1. Date precision: prefer year; range OK.",
  "input_schema": {
    "type": "object",
    "properties": {
      "person_index": {"type": "integer", "minimum": 1},
      "event_type": {"type": "string", "enum": ["birth", "death", "marriage", "migration", "occupation", "other"]},
      "date_start_year": {"type": "integer", "minimum": 1500, "maximum": 2100},
      "date_end_year": {"type": "integer", "minimum": 1500, "maximum": 2100},
      "place_index": {"type": "integer", "minimum": 1, "description": "1-based index into pass-1 places[]"},
      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
      "evidence_snippets": {"type": "array", "items": {"type": "string"}, "minItems": 1}
    },
    "required": ["person_index", "event_type", "confidence", "evidence_snippets"]
  }
}
```

### `flag_uncertain` (все 3 pass'а)

```json
{
  "name": "flag_uncertain",
  "description": "Use when the transcript mentions something genealogically relevant but you cannot fit it into the structured tools (e.g. ambiguous pronoun, contradictory dates). The reviewer will resolve manually.",
  "input_schema": {
    "type": "object",
    "properties": {
      "category": {"type": "string", "enum": ["ambiguous_reference", "contradiction", "unparseable_date", "unknown_relation", "other"]},
      "note": {"type": "string"},
      "evidence_snippets": {"type": "array", "items": {"type": "string"}, "minItems": 1}
    },
    "required": ["category", "note", "evidence_snippets"]
  }
}
```

> **Per-pass allowlist:** pass 1 → `{create_person, add_place, flag_uncertain}`;
> pass 2 → `{link_relationship, flag_uncertain}`; pass 3 → `{add_event, flag_uncertain}`.
> Передавай только разрешённые tools в `tools=` каждого call'а — это и
> guard, и cost-saver (модель не «изобретает» лишних шагов).

## ENDPOINTS

`services/parser-service/src/parser_service/api/voice_extraction.py`:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/audio-sessions/{id}/extract` | Триггер extraction job; idempotent: если уже есть active job на этой session — возвращает существующий extraction_job_id |
| `GET` | `/audio-sessions/{id}/extractions` | список proposals по session, group-by extraction_job_id |
| `GET` | `/extractions/{extraction_job_id}` | proposals одного job'а (для 10.9c review queue) |

Permission gate (как у audio-sessions): EDITOR для POST, VIEWER для GET.

POST body (минимальный):

```python
class StartExtractionRequest(BaseModel):
    # Опционально — owner может попросить re-extract на изменённом transcript'е
    # (это уже 10.9d feature; здесь оставляем поле, но игнорируем если transcript
    # не менялся между job'ами).
    force: bool = False
```

POST response:

```python
class ExtractionJobResponse(BaseModel):
    extraction_job_id: UUID
    audio_session_id: UUID
    status: Literal["queued", "running", "succeeded", "partial_failed", "cost_capped", "failed"]
    created_at: datetime
```

**Critical privacy gate (повторяем паттерн из 10.9a #3 §A.2):**
session.transcript_text должен существовать (status=`ready`); если
`audio_session.consent_egress_at IS NULL` — 403, даже несмотря на то,
что текст уже не уезжает в OpenAI. Anthropic — тоже egress, и для тех
же AJ-чувствительных PII действует тот же gate (см. ADR-0064 §3.6).

## WORKER — `audio.nlu_extract` arq job

Файл: `services/parser-service/src/parser_service/jobs/voice_extract.py`.

```python
async def voice_extract_job(ctx: JobCtx, extraction_job_id: UUID) -> dict:
    """3-pass NLU extraction.

    Алгоритм:
        1. Load audio_session via FK chain (extraction → audio_session_id).
        2. Validate transcript_text != null & status='ready' & consent OK.
        3. Pre-flight cost cap: token-estimate transcript; abort if > cap.
        4. Pass 1 (entities) → save N proposals (proposal_type ∈ {person, place}).
        5. Pass 2 (relationships) → save proposals (proposal_type=relationship).
        6. Pass 3 (temporal-spatial) → save proposals (proposal_type=event).
        7. log_ai_usage в Redis после каждого pass'а.
        8. Final status: succeeded | partial_failed | cost_capped.

    Retry policy: 1 попытка per pass; на pass-fail — partial_failed
    (не вся session повторяется).
    """
```

Регистрация в `WorkerSettings.functions`. Не enqueue'ить автоматически
из transcribe-job — extraction триггерится ЯВНО через POST endpoint
(owner-controlled cost). Это отличается от 10.9a, где transcribe
автоматический.

## TESTS

### Unit — `packages/ai-layer/tests/`

`test_voice_to_tree_extract.py`:

- Mock Anthropic SDK; happy path всех 3 pass'ов → 3-pass output корректен.
- Pass 2 пытается вызвать `create_person` (не из allowlist) → tool-use
  игнорируется, телеметрия пишет `unexpected_tool`.
- Pass 1 happy, pass 2 5xx → `partial_failed`, pass-1 proposals
  сохранены.
- Pre-flight cost cap превышен → `VoiceExtractCostCapError` без
  Anthropic-вызова.
- AI_DRY_RUN=true без api_key → mock-payload (deterministic; см.
  ADR-0057 §D).

### Fixtures (тексты — в `packages/ai-layer/tests/fixtures/voice_transcripts/`)

3 обязательных fixture'а — каждый ≤500 слов, без реальных PII:

1. **`ru_simple.txt`** — single-speaker RU, 3–4 person'а, 1 marriage,
   1 birth-year date, 1 city. Acceptance: pass-1 ≥3 persons,
   pass-2 ≥1 relationship, pass-3 ≥1 event.
2. **`en_simple.txt`** — single-speaker EN, similar shape, для
   проверки что prompt language-agnostic (system-prompt EN, transcript
   EN).
3. **`mixed_ru_he.txt`** — RU базовый + еврейские имена / shtetl-названия
   на иврите-в-латинице («Berdichev», «Shmuel ben Avraham»). AJ-fixture
   per memory: owner reviews через AJ lens.
   Acceptance: place_type=`shtetl` хотя бы один; transliterated имя
   нормально парсится в person с `surname` или `patronymic`.

### Integration — `services/parser-service/tests/`

`test_voice_extraction_api.py`:

- POST без consent на session → 403 `consent_required` (даже если
  transcript уже есть).
- POST с status=`uploaded` (transcript ещё нет) → 409 `transcript_not_ready`.
- POST happy → enqueue arq job, 202 Accepted с extraction_job_id.
- GET extractions group-by job → корректно группирует.

## ADR — `docs/adr/00XX-voice-to-tree-nlu-extraction.md`

Новый ADR (номер на момент write — `git pull origin main` и берёшь
следующий свободный; на 2026-05-01 это будет вероятно 0066+).
Обязательные секции:

- **Контекст:** post-demo продолжение ADR-0064; demo показал что
  transcript работает, теперь нужен NLU.
- **Решение:** 3-pass pipeline — обоснование выбора (single-pass
  слишком жадный на 4k context, agentic loop слишком дорогой и
  непредсказуемый по cost; 3-pass — sweet spot).
- **Cost cap:** ссылка на ADR-0064 §3.6 + точные числа из этого
  брифа (`VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION` etc.).
- **Privacy:** Anthropic как второй egress-канал — тот же consent gate.
- **Что НЕ закрыто:** review queue (10.9c), re-extract после edit (10.9d),
  name-engine integration (Phase 15.10).
- **Когда пересмотреть:** WER на mixed_ru_he > 30%; cost > $X/session
  на bета — переход на cheaper model (Haiku-tier) для passes 2/3.

## ANTI-DRIFT — что НЕ трогать в этом PR

- **❌ Tree assembly** (создание `Person` / `Family` / `Event` ORM-rows на
  основе proposals) — это **agent #08 / Phase 10.9c-cold**. Здесь —
  ТОЛЬКО эмит proposals в `voice_extracted_proposals`. Никаких
  `db.add(Person(...))`.
- **❌ UI / frontend** — read-only transcript уже scaffolded в брифе #4
  (Phase 10.9a web UI). Review queue над proposals — Phase 10.9d
  (отдельный бриф). Не правь `apps/web/`, не добавляй i18n keys.
- **❌ Name engine integration** (Phase 15.10) — append-mode territory.
  Не нормализуй имена через `name_engine`, не дедуплицируй persons по
  фонетике, не вызывай DM-soundex. Proposals остаются raw — review
  queue (10.9c) или name-engine (15.10) разрулит.
- **❌ Streaming / agentic multi-turn** — ADR-0064 §4 явно: out of scope.
  Один pass — один Anthropic call.
- **❌ Self-hosted Whisper / faster-whisper** — другая фаза (10.9.x
  privacy-tier). Этот бриф работает поверх готового OpenAI Whisper
  output'а из 10.9a.

## SELF-VERIFY — чеклист до открытия PR

- [ ] `git fetch origin main && git rebase origin/main` — на freshest main.
- [ ] PR #164 (10.9a ORM) merged → `AudioSession` impo'ртится без `# type: ignore`.
- [ ] `voice_extracted_proposals` добавлено в `SERVICE_TABLES` allowlist
      (`tests/test_schema_invariants.py`) — иначе CI падает.
- [ ] `uv run pytest packages/ai-layer -v` — passing, включая 3 fixture'а.
- [ ] `uv run pytest services/parser-service -v -k voice_extraction` — passing.
- [ ] `uv run mypy packages/ai-layer packages/shared-models services/parser-service` — strict.
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run alembic upgrade head` локально без ошибок;
      `uv run alembic downgrade -1 && uv run alembic upgrade head` — round-trip.
- [ ] `pwsh scripts/check.ps1` (Windows) или `bash scripts/check.sh` (Unix) — green.
- [ ] `uv run pre-commit run --files <ваши_файлы>` — passing.
- [ ] PR-описание ссылается на: ADR-0064 §3.6, новый ADR-00XX,
      feature_voice_to_tree.md §4, PR #164.
- [ ] PR-описание явно: «Tree assembly (Person/Event creation) — out of scope;
      proposals only. Review queue — Phase 10.9c (отдельный PR).»
- [ ] Cost-budget alert: 3 passes × ~$0.05–0.08 input + ~$0.02 output
      per session — закладывай в `pricing.py` оценку, иначе runaway-cost
      на bет'е.

## PR TITLE (Conventional Commits)

```text
feat(ai-layer,parser-service,shared-models): phase 10.9b — voice-to-tree NLU 3-pass extraction (proposals only) + ADR-00XX
```

Альтернативные коммит-сообщения внутри PR'а:

```text
feat(shared-models): add voice_extracted_proposals ORM (Phase 10.9b)
feat(ai-layer): add 3-pass voice_to_tree_extract use case + tool schemas
feat(parser-service): add /audio-sessions/{id}/extract endpoint + arq job
test(ai-layer): add voice extraction fixtures (ru_simple, en_simple, mixed_ru_he)
docs(adr): add ADR-00XX voice-to-tree NLU extraction (3-pass rationale)
```
