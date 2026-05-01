# Feature spec — Phase 10.9: voice-to-tree

> **Status:** DRAFT — pending owner sign-off на §«Open decisions».
> **Date:** 2026-05-01
> **Authors:** @autotreegen (drafted by Claude after restart of 6 stalled agents)
> **Tags:** `ai`, `audio`, `whisper`, `phase-10.9`, `privacy`, `aj-genealogy`, `demo-2026-05-06`
> **Связанные ADR (предлагается создать после approve):** ADR-0064 (voice-to-tree pipeline). Номера 0062/0063 заняты gedcom-quarantine + dna-autoclusters, выкатились параллельно 01.05.

---

## 0. TL;DR

Owner записывает голосом семейные истории → Whisper транскрибирует → LLM
извлекает кандидатов в персон / события / связи → owner ревьюит → коммит
в дерево. Это **первый AI use case с egress аудио во внешний сервис**, поэтому
consent-gate жёсткий: до явного согласия запись запрещена на UI-уровне.

**Подфазы (4 куска, не один PR):**

| Подфаза | Scope | Длительность | Demo 06.05? |
|---|---|---|---|
| **10.9a** | Audio upload + Whisper STT + persisted read-only transcript | 4–5 дней | ✅ да |
| **10.9b** | LLM-extraction transcript → person/event/relation candidates (proposals, не committed) | 5–7 дней | ❌ нет |
| **10.9c** | Review queue UI + merge candidates в дерево (через Hypothesis или прямой apply) | 5–7 дней | ❌ нет |
| **10.9d** | Transcript editor + re-extract после правок | 3–4 дня | ❌ нет |

**Single critical-path до 06.05:** только 10.9a. 10.9b–d — после демо, sequential.

---

## 1. Контекст и why now

- ICP проекта — **AJ-genealogy** (Ashkenazi-Jewish): диаспоральная,
  устные истории — частично основной носитель данных. У владельцев
  деревьев десятки часов воспоминаний родственников, которые сейчас
  существуют только как файлы на диске. Voice-to-tree снимает фрикцию
  «слушать → перепечатывать в формы». По ROI это самый мощный AI use
  case после §14.1.4 hypothesis-explainer.
- **Демо 2026-05-06:** owner показывает MVP инвестору / партнёру.
  Демо-сценарий — owner говорит 30–60 секунд по-русски о прадеде,
  через ≤30 сек на экране появляется транскрипт. Ничего больше.
- Anthropic не делает STT, Voyage не делает STT (см. ADR-0043). Значит
  любой кандидат на STT — это **новый внешний egress** за пределами
  существующих провайдеров.
- ДНК (Art. 9) и так передаётся жёстко-консервативно (ADR-0043 §Privacy);
  голос — биометрический сигнал и теоретически может быть Art. 9 при
  voice-id обработке. Whisper voice-id не делает, но retention 30 дней
  у API-сервисов остаётся PII-риском. AJ-аудитория особенно чувствительна
  к утечкам PII — эта чувствительность зашита в product-стратегию.

---

## 2. Архитектура (high-level)

```text
[apps/web]                                          [services/parser-service]
   │                                                       │
   │ 1) GET /trees/{id}/audio-consent  ── читает consent ──▶
   │                                                       │
   │ 2) POST /trees/{id}/audio-consent  ── ставит timestamp▶
   │                                                       │
   │ 3) <MediaRecorder> WebM/Opus ──┐                      │
   │                                │ 4) POST .../audio-sessions
   │                                └──▶ multipart ────────▶ AudioSession (status=uploaded)
   │                                                       │      │
   │                                                       │      └──▶ MinIO/GCS put_object
   │                                                       │
   │                                                arq enqueue: transcribe_audio_session(id)
   │                                                       │
   │                                              ┌────────▼────────┐
   │                                              │   arq worker    │──▶ OpenAI Whisper API
   │                                              │ packages/ai-    │   (or local faster-whisper)
   │                                              │  layer.clients  │
   │                                              └────────┬────────┘
   │                                                       │ status=ready, transcript_text, language
   │                                                       ▼
   │ 5) GET /audio-sessions/{id} (poll)  ◀─── AudioSession ─┘
   ▼
   [transcript view: read-only]
```

10.9b добавляет шаг 6 (transcript → LLM extraction → ProposalSet),
10.9c — review queue над ProposalSet, 10.9d — edit transcript + re-extract.

---

## 3. Phase 10.9a — детальный scope (DEMO критпуть)

### 3.1 User flow

1. Owner открывает страницу дерева → видит баннер «Voice-to-tree (beta)»
   с **выключенной** кнопкой Record и текстом consent (см. §3.5).
2. Owner кликает «I consent» → бекенд ставит `consent_egress_at = now()`
   в `tree_settings` (или per-user). Кнопка Record активируется.
3. Owner записывает 10 сек – 5 мин аудио (MediaRecorder, WebM/Opus).
4. Frontend POST'ит multipart на `/trees/{id}/audio-sessions`.
5. Серверная ORM-запись `AudioSession` (status=`uploaded`), enqueue arq job.
6. UI показывает spinner с прогрессом (poll `GET /audio-sessions/{id}` каждые 3 сек).
7. Worker транскрибирует через Whisper, обновляет `transcript_text`, `language`,
   `status=ready`.
8. UI рендерит transcript **read-only** (без редактирования; редактирование — 10.9d).

### 3.2 Data model — новая ORM-таблица `audio_sessions`

```python
# packages/shared-models/src/shared_models/orm/audio_session.py
class AudioSession(Base, SoftDeleteMixin, ProvenanceMixin):
    __tablename__ = "audio_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tree_id: Mapped[UUID] = mapped_column(ForeignKey("trees.id"), index=True)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))

    # хранилище
    storage_uri: Mapped[str]                     # s3://bucket/audio/{uuid}.webm
    mime_type: Mapped[str]                       # audio/webm; codecs=opus
    duration_sec: Mapped[float | None]
    size_bytes: Mapped[int]

    # транскрипция
    status: Mapped[AudioSessionStatus]           # enum: uploaded|transcribing|ready|failed
    language: Mapped[str | None]                 # 'ru', 'en', ... (Whisper-detected)
    transcript_text: Mapped[str | None]
    transcript_provider: Mapped[str | None]      # 'openai-whisper-1' | 'faster-whisper-large-v3'
    transcript_model_version: Mapped[str | None]
    transcript_cost_usd: Mapped[Decimal | None]  # для cost-telemetry (ADR-0057 паттерн)
    error_message: Mapped[str | None]

    # privacy gate (см. §3.5)
    consent_egress_at: Mapped[datetime | None]   # копия из tree_settings на момент записи
    consent_egress_provider: Mapped[str | None]  # 'openai' | 'self-hosted-whisper'

    # provenance / soft-delete унаследовано из миксинов

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(onupdate=func.now())
    deleted_at: Mapped[datetime | None]
```

`tree_settings` (или `user_settings` — см. Open decision #2) получает
поле `audio_consent_egress_at: datetime | None` + `audio_consent_egress_provider: str | None`.

Alembic-миграция: одна, additive, безопасная (новая таблица + новое поле в
существующей).

### 3.3 API endpoints (parser-service)

| Method | Path | Назначение |
|---|---|---|
| `GET` | `/trees/{id}/audio-consent` | читать текущий consent state |
| `POST` | `/trees/{id}/audio-consent` | ставить `consent_egress_at = now()` (idempotent: if already set, return current) |
| `DELETE` | `/trees/{id}/audio-consent` | revoke + GDPR-erasure всех `audio_sessions` этого дерева (см. §3.6) |
| `POST` | `/trees/{id}/audio-sessions` | multipart upload (требует non-null consent), создаёт ORM, enqueue job |
| `GET` | `/trees/{id}/audio-sessions` | список (paginated) |
| `GET` | `/audio-sessions/{id}` | single, с transcript |
| `DELETE` | `/audio-sessions/{id}` | soft-delete (+ stub для hard-delete worker'а — см. ADR-0049) |

Permission gate: `require_tree_role(TreeRole.EDITOR)` (как у `/persons`).

### 3.4 Whisper-интеграция

Новый клиент в `ai-layer`:

```text
packages/ai-layer/src/ai_layer/clients/whisper.py
  └ WhisperClient.transcribe(audio_bytes, mime_type) -> TranscriptResult
packages/ai-layer/src/ai_layer/use_cases/transcribe_audio.py
  └ AudioTranscriber.run(session: AudioSession) -> AudioSession (mutated)
```

Параметры (вынести в `ai-layer.config`):

- `WHISPER_PROVIDER` = `openai` | `faster-whisper` (см. Open decision #1)
- `WHISPER_MODEL` = `whisper-1` (OpenAI) или `large-v3` (local)
- `WHISPER_MAX_DURATION_SEC` = `600` (10 минут — capping для cost)
- `AI_DRY_RUN` уже существует (ADR-0057 §D); мокаем транскрипт.

Pricing telemetry — повторно используем `ai_layer.telemetry.log_ai_usage`
(ADR-0057 §C, Redis-list). Use_case = `transcribe_audio`.

**arq job:** `services/parser-service/.../jobs/transcribe_audio.py` —
загружает байты из MinIO, вызывает `AudioTranscriber.run`, обновляет ORM.
Retry policy: 3 попытки, экспоненциальный backoff. На finalfail → `status=failed`,
`error_message` с категорией (`whisper_5xx`, `audio_corrupt`, `over_quota`, ...).

### 3.5 Consent gate (privacy non-negotiable)

Текст consent (RU + EN, i18n уже катит после Phase 4.13b):

> «Запись будет отправлена в [OpenAI Whisper / собственный сервер] для
> расшифровки. [OpenAI хранит запросы 30 дней по политике Standard tier /
> На собственном сервере — без retention.] После расшифровки текст
> хранится в вашей зоне дерева. Вы можете отозвать согласие в настройках,
> что удалит все записи.»

UI-инварианты:

- Кнопка Record **визуально disabled** + tooltip «Требуется согласие на egress
  аудио» при `consent_egress_at IS NULL`.
- Backend дублирует: `POST /audio-sessions` без consent → `403 consent_required`.
  (defence-in-depth: фронт мог быть скомпрометирован.)
- При revoke (`DELETE /audio-consent`) — async erasure всех `audio_sessions`
  этого дерева через arq job (паттерн ADR-0049 erasure pipeline). Owner
  получает email-confirmation.

### 3.6 Privacy / data retention

- **Не отправлять** аудио во внешний сервис, пока не записан
  `consent_egress_at`. Backend-валидатор обязателен.
- **На раскрутку 10.9a — без redaction** транскрипта (имена / места уйдут
  в Whisper logs). Это допустимо для beta-owners (один user — owner
  проекта). Public-tree share (ADR-0047) — out of scope для voice-to-tree
  до Phase 10.9c.
- **Soft-delete по умолчанию.** Hard-delete attached к GDPR erasure
  pipeline (ADR-0049): drop из MinIO + DB + Redis-телеметрия.
- **DNA-сегменты в транскрипте** (если owner продиктует «у нас 1300 cM с
  Иваном») — пока не редактируем. Caveat в consent-тексте: «не диктуйте
  ДНК-числа, если не хотите, чтобы они попали в Whisper logs».
- **Retention в Whisper:** OpenAI — 30 дней (Standard); Anthropic ZDR
  unrelated. Если 10.9a катимся на OpenAI — фиксируем это в ADR.

### 3.7 Tests / acceptance

| Уровень | Что проверяется |
|---|---|
| unit | ORM `AudioSession` round-trip + soft-delete + provenance |
| unit | `WhisperClient` mock-driven (status codes, malformed response, retry) |
| unit | `AudioTranscriber.run` happy path + each fail-category |
| integration | POST without consent → 403 |
| integration | POST → arq enqueue → status transitions uploaded→transcribing→ready |
| integration | DELETE consent → erasure job removes file from MinIO + ORM |
| e2e (Playwright) | Record 5 сек → upload → poll → transcript visible |

**Acceptance gate для 06.05 demo:** на staging записать 30-секундный
RU-aудио, через ≤30 сек видеть транскрипт. Рассмотрены 3 пути:
RU dialect, mixed RU+EN, тихий фон.

### 3.8 LOC / time estimate

| Слой | Файлы | LOC |
|---|---|---|
| ORM + миграция | `audio_session.py`, `tree_settings` patch, alembic-revision | ~150 |
| ai-layer | `clients/whisper.py`, `use_cases/transcribe_audio.py`, config patch | ~250 |
| parser-service | `api/audio_sessions.py`, `api/audio_consent.py`, `jobs/transcribe_audio.py`, `schemas` patch, router register | ~400 |
| apps/web | recorder component, consent banner, sessions list, transcript view, i18n keys | ~600 |
| tests | unit + integration + 1 e2e | ~500 |
| **Итого** | — | **~1900** |

5 рабочих дней до 06.05 — **возможно при ноль unknowns**, поэтому Open
decisions ниже надо закрыть **сегодня**.

---

## 4. Phase 10.9b — outline (post-demo)

- LLM use_case `voice_to_tree_extract.py` в `ai-layer`: transcript + tree
  context (родственники, известные имена) → JSON `{persons[], events[],
  relationships[]}` с per-item `confidence` и `evidence_snippets`.
- ORM: `voice_extracted_proposals` (tree_id, audio_session_id, payload jsonb,
  status: pending | approved | rejected). НЕ создаём persons / events
  напрямую — только proposals.
- Cost cap per session (паттерн ADR-0057): top-N сегментов транскрипта,
  truncate до 4k токенов.
- Privacy: те же rules — egress только при consent.
- Out: streaming / агентные tools — не нужны.

## 5. Phase 10.9c — outline

- UI review queue над `voice_extracted_proposals`.
- Каждый proposal = card: «Похоже, "Иван Соломонов" (1850–1920) — отец
  Сары» → owner кликает Approve / Reject / Edit.
- Approve → создаёт `Hypothesis` (Phase 7.2 паттерн) с источником
  `audio_session_id` в evidence-graph. Hypothesis ревью идёт через уже
  существующий Phase 4.9 review UI — **не дублируем**.

## 6. Phase 10.9d — outline

- Transcript editor (на странице session) — правки сохраняются как
  `transcript_text` revision.
- Кнопка «Re-extract» → перезапускает 10.9b на изменённом тексте.
- Audit-log изменений транскрипта.

---

## 7. Open decisions (требуются ответы owner'а до старта 10.9a)

| # | Вопрос | Варианты | Рекомендация |
|---|---|---|---|
| 1 | **STT провайдер** | (A) OpenAI Whisper API; (B) self-hosted faster-whisper на GCP; (C) оба, env-flag | (A) для 10.9a — fastest path to demo; (B) на пост-демо для AJ ICP privacy-сценариев |
| 2 | **Consent scope** | (A) per-tree (`tree_settings.audio_consent_egress_at`); (B) per-user (`user_settings…`); (C) per-session checkbox каждый раз | (A) — UX-friendly, soft-revoke есть |
| 3 | **Storage backend** | (A) MinIO local + GCS prod (как сейчас sources); (B) GCS прямо | (A) — повторяем sources pattern, ноль новой инфры |
| 4 | **Где живёт arq worker** | (A) parser-service worker (текущий); (B) новый `voice-service` микросервис | (A) — 5 дней до демо, ноль времени на новый сервис |
| 5 | **Должно ли 10.9a уметь редактировать транскрипт** | (A) нет, read-only — agent recap явно так и сказал; (B) inline edit | (A) — соответствует halt-сообщению агента 10.9d |

Дефолты выше — мои; если owner подтверждает все — пишу ADR-0064 +
открываю PR на 10.9a.

---

## 8. Risks & mitigations

| Риск | Severity | Митигация |
|---|---|---|
| OpenAI Whisper квота / 5xx во время демо | High | Pre-warm на staging за 24 ч; fallback на cached transcript если failed |
| Browser MediaRecorder инконсистентность (Safari WebM не поддерживает) | Medium | Demo на Chrome / Firefox; в roadmap — fallback на mp4/aac для Safari в 10.9d |
| Privacy-инцидент: аудио улетает без consent | **Critical** | Backend-валидатор (defence-in-depth) + integration test «POST without consent → 403» — обязательный gate в CI |
| AJ-аудитория узнаёт «вы шлёте мои семейные истории в OpenAI» | High | Прозрачный consent-текст; roadmap-записка о self-hosted Whisper в 10.9b |
| Cost runaway (owner записывает часами) | Medium | `WHISPER_MAX_DURATION_SEC = 600` cap + Redis-телеметрия + daily-budget alert |
| 5 дней не хватит | Medium | Сократить scope: убрать revoke-flow (Phase 10.9d), оставить only POST/GET |
| LLM-галлюцинация в транскрипте (Whisper не галлюцинирует обычно, но на тихом RU может) | Low | Acceptance test покрывает «тихий фон»; в UI явная пометка «авто-транскрипт, проверьте» |

---

## 9. Out of scope для всей Phase 10.9

- Speaker diarization (распознавание «кто говорит» — два голоса).
- Real-time streaming транскрипция.
- Voice-id (биометрика) — категорически out, Art. 9.
- Voice cloning / TTS обратного направления.
- Языки помимо RU/EN/UK/PL/HE на 10.9a–b (Whisper их распознаёт, но
  acceptance-tests только на RU+EN до Phase 10.9d).

---

## 10. Связанные артефакты

- `ROADMAP.md` §14.1 (AI use cases — voice-to-tree не вписан явно;
  предлагаю добавить пункт 7 в §14.1 после approve этой спеки).
- ADR-0043 (ai-layer architecture) — Whisper-клиент расширяет paragraph
  «дополнительные провайдеры».
- ADR-0049 (GDPR erasure pipeline) — переиспользуем для DELETE consent.
- ADR-0057 (hypothesis explainer) — паттерн: AI use case + Redis-телеметрия
  - dry-run env + soft-fail.
- `.agent-tasks/` — после approve переносим этот документ в
  `docs/agent-briefs/phase-10-9-voice-to-tree.md` и архивируем
  устаревшие 6 stale brief'ов от 30.04.

---

## 11. Чеклист «можно стартовать 10.9a»

- [ ] Owner закрыл Open decisions §7 (5 вопросов).
- [ ] `git pull origin main` на main (локально behind 2).
- [ ] Worktree `phase-10-9a-voice-to-tree-audio` создан от свежего main.
- [ ] ADR-0064 (voice-to-tree pipeline) написан и committed первым PR
      (отдельным от кода — чтобы ревью архитектуры не блокировало код).
- [ ] OpenAI API key выдан (если decision #1 = A) и положен в `.env` +
      Secret Manager-плейсхолдер для прода.
- [ ] `.agent-tasks/*.md` старые архивированы в `docs/archive/agent-tasks-2026-04/`.
