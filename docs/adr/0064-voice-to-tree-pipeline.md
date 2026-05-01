# ADR-0064: Voice-to-tree pipeline — STT egress, consent gate, storage (Phase 10.9a)

> **Note (2026-05-01):** этот ADR изначально был написан как 0062, но в момент
> финализации в `main` параллельно прилетели 0062-gedcom-quarantine-roundtrip
> (Phase 5.5a) и 0063-dna-autoclusters-and-endogamy (Phase 6.7a). Перенумерован
> в 0064. Не путать с прежними внутренними упоминаниями «ADR-0062» в спеке —
> `docs/feature_voice_to_tree.md` обновлён.

- **Status:** Proposed
- **Date:** 2026-05-01
- **Authors:** @autotreegen
- **Tags:** `ai`, `audio`, `whisper`, `phase-10.9`, `privacy`, `gdpr`, `aj-genealogy`

## Контекст

Phase 10.0 (ADR-0043) и 10.1 (ADR-0057) посадили AI-foundation: Anthropic
для рассуждений, Voyage для эмбеддингов, prompt-registry, pricing-таблица,
Redis-телеметрия, `AI_DRY_RUN`. STT (speech-to-text) намеренно не входил
в 10.0, потому что ни Anthropic, ни Voyage его не делают. Phase 10.9
открывает первый use case с **аудио-egress**.

ICP проекта — **AJ-genealogy** (Ashkenazi-Jewish): диаспоральная
аудитория, чьи семейные истории живут в виде устных воспоминаний
старших родственников (десятки часов аудио на дисках владельцев деревьев).
Voice-to-tree снимает фрикцию «слушать и перепечатывать» и потенциально —
самый ROI-ёмкий AI use case после hypothesis-explainer'а (ADR-0057).
Одновременно AJ-сегмент исторически сверхчувствителен к утечкам PII;
любая эгресс-история в 10.9 должна стоять на жёстком consent-gate.

**Демо 2026-05-06** ставит хард-дедлайн: запись 30–60 секунд RU →
транскрипт за ≤30 сек. Это формирует scope **10.9a** — audio upload +
Whisper + read-only transcript. Подфазы 10.9b–d (LLM-extraction,
review queue, transcript editor) идут после демо и имеют отдельные
ADR-якоря (TBD).

Силы давления:

- **Privacy non-negotiable.** Аудио — потенциально биометрический
  сигнал; транскрипт содержит имена, даты, места — PII. Любой провайдер
  (включая OpenAI Whisper API — Standard tier, 30-day retention) держит
  логи дольше, чем приемлемо для пользовательских данных нашего ICP без
  явного согласия. Defence-in-depth: фронтенд блокирует кнопку Record;
  бекенд отбивает 403 без `consent_egress_at`.
- **5 рабочих дней до демо.** Ноль времени на новую инфру (новый
  микросервис, GPU-узел под self-hosted Whisper). Любое решение, которое
  требует > 1 дня инфра-работы, валит таймлайн.
- **Cost.** OpenAI Whisper API — $0.006/мин. Owner оценивает ≤ 60 минут
  аудио / день на bета → $0.36/день. Acceptable. Self-hosted faster-whisper
  на GCP — $X (зависит от GPU SKU); даже A2-highgpu-1 даёт fixed ~$1.5/час
  uptime, что на режиме «несколько минут / день» катастрофически
  невыгодно.
- **DNA-leakage риск.** Owner может голосом продиктовать «у нас 1300 cM с
  Иваном». DNA — Art. 9 special category (ADR-0043 §Privacy). Полный
  redaction-layer на 10.9a отложен (как в ADR-0057 §E); митигация —
  предупреждение в consent-тексте.
- **Telemetry continuity.** Уже есть `ai_layer.telemetry.log_ai_usage`
  (ADR-0057 §C, Redis-list). Не плодим параллельный учёт.

## Рассмотренные варианты

### A. STT провайдер

- **OpenAI Whisper API (`whisper-1`) (выбрано):**
  - ✅ Drop-in: HTTP+API-key, ноль инфры. Совместимо с 5-дневным дедлайном.
  - ✅ Качество RU/EN/HE/PL — топ для диктовки разговорной речи.
  - ✅ $0.006/мин — самый дешёвый managed-вариант на 2026-04-30.
  - ❌ Standard-tier retention 30 дней. Privacy-debt принимается на 10.9a;
    self-hosted в roadmap (10.9b+ или 10.9.x).
  - ❌ Новый внешний провайдер в стеке — расширение surface-area для
    инцидентов и compliance-вопросов.
- **Self-hosted faster-whisper на GCP A2 (отвергнуто на 10.9a):**
  - ✅ Ноль egress, ноль retention.
  - ❌ GPU-узел + image + autoscaler + cold-start latency — это 3–5 дней
    инфра-работы на пустом месте. Демо умирает.
  - ✅ Запланирован на 10.9.x как «privacy-tier» опция за пейволлом.
- **Anthropic + OCR-фолбек на текстовую расшифровку (отвергнуто):**
  - ❌ Ставит ручной шаг между записью и транскриптом. Не закрывает demo.

### B. Consent scope

- **Per-tree (`tree_settings.audio_consent_egress_at`) (выбрано):**
  - ✅ UX: один клик «I consent» на дерево → запись становится доступной
    для всех записей этого дерева. Отзыв одной кнопкой.
  - ✅ Согласуется с моделью permissions (ADR-0036 sharing) — права уже
    привязаны к tree-level.
  - ❌ Требует мигрировать `tree_settings` (или создать таблицу, если
    её нет — TBD при имплементации). Минимальная alembic-стоимость.
- **Per-user (`user_settings.audio_consent_egress_at`):**
  - ✅ Простая ORM-структура (один user → один флаг).
  - ❌ Owner с двумя деревьями (one personal, one shared) не может
    дифференцировать consent. AJ-кейс: личное дерево с DNA-инфой vs
    демо-дерево для родственников — нужен разный риск-профиль.
- **Per-session checkbox каждый раз:**
  - ❌ UX-trash. Owner отжмёт через два раза и перестанет читать текст.
    Снижает значимость consent до боль-pasta.

### C. Storage backend для аудио-файлов

- **MinIO local + GCS prod (как `sources` сейчас) (выбрано):**
  - ✅ Повторяем существующий паттерн (parser-service + sources). Ноль
    новой инфраструктуры, известный SRE-контур.
  - ✅ Dev-loop работает офлайн через MinIO в `docker compose`.
  - ✅ Erasure-pipeline (ADR-0049) уже умеет `s3_delete` для GCS-bucket.
- **GCS прямо (без MinIO в dev):**
  - ❌ Dev требует подключения к GCS — ломает офлайн-разработку и
    multiplies cost при сценарии когда несколько разработчиков отлаживают
    storage path.
- **DB-blob (bytea):**
  - ❌ Postgres bloat на 100 МБ-файлах. Не вариант.

### D. Где живёт arq worker для transcribe-job

- **parser-service worker (выбрано):**
  - ✅ Уже есть arq-runner в parser-service, есть Redis-queue. Ноль
    новой инфры.
  - ✅ Worker имеет доступ к ORM (ему нужно мутировать `AudioSession`).
- **Новый микросервис `voice-service`:**
  - ✅ Корректный domain-split (audio — отдельный bounded context).
  - ❌ 5 дней до демо; новый сервис = 1–2 дня инфра-работы (Dockerfile,
    deploy, health-check, k8s манифест). Не лезет.
  - ✅ Запланирован на 10.9.x когда воркод вырастет за пределы 1
    транскрипции / минуту.

### E. Editable transcript в 10.9a

- **Read-only (выбрано):**
  - ✅ Совпадает с halt-сообщением остановленного агента 10.9d, который
    сам вывел: «10.9a stops at transcript persisted, viewable read-only».
  - ✅ Минимум UI-кода → реалистично за 5 дней.
  - ✅ Чёткая граница для post-demo инкремента (10.9d добавит editor).
- **Inline edit с сохранением:**
  - ❌ Расширяет UI-scope; нужны concurrency-rules (revision-history) и
    audit-log изменений транскрипта. Не на 10.9a.

### F. Retention аудио-файла после транскрипции

- **Audio + transcript хранятся вместе с tree до soft-delete (выбрано):**
  - ✅ Owner может переслушать: совпадает ли транскрипт с источником.
    Это критично для evidence-first принципа (CLAUDE.md §3) — транскрипт
    без оригинала непроверяем.
  - ✅ Соответствует существующей multimedia-модели (ADR — Phase 3.5).
  - ❌ Удлиняет retention-окно для биометрики. Митигация — UI явно
    показывает «у вас 12 минут аудио» + кнопка «удалить аудио,
    оставить транскрипт».
- **Aggressive — удалить аудио сразу после успешной транскрипции:**
  - ✅ Минимизирует биометрику в нашем хранилище.
  - ❌ Ломает аудит «правильно ли расшифровал Whisper». Для AJ-аудитории
    с ивритом / польскими диалектами Whisper может галлюцинировать —
    оригинал необходим для верификации.
- **Eternal без soft-delete:**
  - ❌ Несовместимо с ADR-0049 erasure-pipeline.

### G. Failure-mode при ошибке Whisper

- **One retry → soft-fail в `AudioSession.status=failed` + UI «Retry»
  (выбрано, паттерн ADR-0057 §F):**
  - ✅ Не обрушает UI; owner видит «не получилось, нажмите Retry».
  - ✅ Telemetry в Redis с категорией ошибки: `whisper_5xx`,
    `audio_corrupt`, `over_quota`, `timeout`.
- **Hard-fail (исключение в worker'е):**
  - ❌ Owner получит «Internal Server Error» без идеи что делать.

## Решение

Выбраны: **A1** (OpenAI Whisper API), **B1** (per-tree consent),
**C1** (MinIO+GCS storage), **D1** (parser-service arq worker),
**E1** (read-only transcript на 10.9a), **F1** (retention с
soft-delete + явная кнопка «удалить аудио»), **G1** (one-retry
soft-fail).

Реализация:

- `packages/shared-models/src/shared_models/orm/audio_session.py` — ORM
  `AudioSession` (см. spec §3.2).
- `packages/shared-models/src/shared_models/orm/tree_settings.py` —
  патч полей `audio_consent_egress_at`, `audio_consent_egress_provider`
  (создать таблицу `tree_settings`, если ещё нет — TBD на старте PR'а).
- `infrastructure/alembic/versions/NNNN_audio_sessions_and_consent.py` —
  одна additive миграция (новая таблица + поля).
- `packages/ai-layer/src/ai_layer/clients/whisper.py` — `WhisperClient`
  (HTTP-клиент через `openai` SDK, dry-run-aware).
- `packages/ai-layer/src/ai_layer/use_cases/transcribe_audio.py` —
  `AudioTranscriber.run(session) -> AudioSession (mutated)`.
- `packages/ai-layer/src/ai_layer/pricing.py` — добавить запись
  `whisper-1 = $0.006/min` + хелпер `estimate_whisper_cost_usd(duration_sec)`.
- `services/parser-service/src/parser_service/api/audio_consent.py` —
  GET/POST/DELETE `/trees/{id}/audio-consent`.
- `services/parser-service/src/parser_service/api/audio_sessions.py` —
  POST/GET/DELETE `/trees/{id}/audio-sessions`, GET `/audio-sessions/{id}`.
- `services/parser-service/src/parser_service/jobs/transcribe_audio.py` —
  arq job, retry policy 3 + exp backoff.
- `services/parser-service/src/parser_service/jobs/erase_audio_session.py` —
  hard-delete при revoke consent (привязан к ADR-0049).
- `apps/web/src/components/voice/recorder.tsx` — MediaRecorder + upload.
- `apps/web/src/components/voice/consent-banner.tsx` — gate UI.
- `apps/web/src/app/trees/[id]/voice/page.tsx` — список сессий +
  read-only transcript view.

OpenAI API key хранится в `.env` (dev) и Secret Manager (prod) под
ключом `OPENAI_API_KEY`. Если ключ отсутствует — `AI_DRY_RUN=true`
автоматически (не нужно дополнительной флага).

Telemetry: `log_ai_usage(use_case="transcribe_audio", model="whisper-1",
input_tokens=0, output_tokens=0, audio_duration_sec=X, cost_usd=Y)`.
В Redis-list `ai_usage:log` (ADR-0057 §C) добавляем поле
`audio_duration_sec`. Backward-compatible — поле опциональное.

## Последствия

### Положительные

- Демо 06.05 разблокировано: critical-path определён, оценка LOC ~1900,
  все технические решения зафиксированы.
- Privacy-gate стоит до любого egress'а — defence-in-depth (UI + backend).
- Erasure-pipeline (ADR-0049) переиспользован «бесплатно»: revoke consent
  → уже существующий worker удаляет MinIO-объекты.
- Voice-to-tree становится первым use case в roadmap, который
  демонстрирует AI-value на demo-сценарии за 60 секунд — критично для
  инвестор-pitch.
- Pricing-таблица расширена на STT-провайдер, что разблокирует
  будущие модальности (TTS если понадобится).

### Отрицательные / стоимость

- **Privacy debt:** аудио + транскрипты с PII улетают в OpenAI на 30
  дней (Standard tier). Beta-only компенсация:
  1. Один-единственный bета-owner на 10.9a (сам владелец проекта).
  2. Явный consent-текст с упоминанием 30-day retention.
  3. Записка в roadmap: self-hosted faster-whisper в 10.9.x как
     privacy-tier опция за пейволлом (ICP-fit для AJ-аудитории,
     которая будет готова платить за «no-egress»).
- **Зависимость от OpenAI uptime во время демо.** Митигация — pre-warm
  на staging за 24 часа + cached transcript fallback.
- **Cost telemetry без агрегатов** (наследовано из ADR-0057 §C). Добавим
  ORM-таблицу `ai_usage_events` в Phase 10.5 как и для prompts.
- **DNA-leakage возможен при диктовке** cM-чисел. Митигация на 10.9a —
  только consent-warning. Redaction-layer — Phase 10.9.x.
- **Audio-blob retention** удлиняет окно биометрики в нашем хранилище.
  Acceptable до Phase 4.11 (GDPR-export covers audio_sessions).

### Риски

- **Egress без consent через bug в backend.** Severity: critical.
  Митигация: integration-тест «POST /audio-sessions без consent → 403»
  обязателен в CI; запрещаем merge без него.
- **OpenAI квота / 5xx во время демо.** Severity: high.
  Митигация: pre-warm staging, mock-fallback в `AI_DRY_RUN`,
  retry-policy 3+exp backoff.
- **Whisper галлюцинирует на тихом RU / иврите / польских диалектах.**
  Severity: medium. Митигация: acceptance-test «тихий фон»; UI помечает
  «авто-транскрипт, проверьте перед использованием»; оригинал-аудио
  доступен для перезапуска (см. §F).
- **AJ-аудитория узнаёт «вы шлёте мои семейные истории в OpenAI».**
  Severity: high. Митигация: прозрачный consent-текст; публичная
  заметка в roadmap про self-hosted Whisper в Phase 10.9.x.
- **Cost runaway если owner запишет часами.** Severity: medium.
  Митигация: `WHISPER_MAX_DURATION_SEC=600` cap + Redis-телеметрия с
  daily-budget alert.
- **Browser MediaRecorder в Safari не пишет WebM.** Severity: medium.
  Митигация: demo на Chrome/Firefox; Safari-fallback (mp4/aac) → 10.9d.

### Что нужно сделать в коде

- 10.9a (эта фаза, по списку §«Решение»).
- 10.9b: ORM `voice_extracted_proposals` + ai-layer use_case
  `voice_to_tree_extract`. Отдельный ADR.
- 10.9c: review queue UI; интеграция в существующий Hypothesis-flow
  (ADR-0021). Отдельный ADR.
- 10.9d: transcript editor + re-extract; Safari-fallback recording.
  Отдельный ADR.
- Phase 10.5: миграция Redis-телеметрии (включая `transcribe_audio`)
  в ORM-таблицу `ai_usage_events`.

## Когда пересмотреть

- **Cost > $50/month на STT** → переключиться на self-hosted faster-whisper
  (вытащить 10.9.x privacy-tier раньше).
- **Privacy-инцидент / жалоба от GDPR-аудита по поводу PII в OpenAI logs**
  → срочно self-hosted; в худшем случае deprecated 10.9a и переезд.
- **Whisper accuracy на RU/HE неприемлема в demo (WER > 15%)** →
  оценить Deepgram, ElevenLabs Scribe, Speechmatics; обновить ADR.
- **Owner-запрос на real-time streaming транскрипцию** → пересмотр D
  (worker placement) — арч-юнит с WebSocket-потоком.
- **Появление on-prem / on-device Whisper-замены сравнимого качества**
  (Distil-Whisper, MLX-Whisper на M-серии) → пересмотр A.

## Ссылки

- Связанные ADR: ADR-0043 (ai-layer architecture), ADR-0049 (GDPR
  erasure pipeline), ADR-0057 (hypothesis explainer — паттерн для
  Redis-телеметрии и dry-run env), ADR-0036 (sharing / permissions —
  per-tree gates), ADR-0021 (hypothesis persistence — паттерн для 10.9c).
- Feature spec: `docs/feature_voice_to_tree.md` (§3 — детальный scope
  10.9a, §7 — Open decisions, закрытые этим ADR).
- ROADMAP §14.1 — AI use cases (voice-to-tree предлагается добавить
  пунктом 7 после approve этого ADR).
- OpenAI Whisper API pricing: <https://openai.com/api/pricing> (snapshot
  2026-04-30, $0.006/min).
- OpenAI data retention (Standard tier, 30 дней):
  <https://platform.openai.com/docs/models/whisper-1>.

## TODO (out-of-band cleanup)

- В `docs/adr/` три ADR с номером 0057 (ai-hypothesis-explanation,
  inference-engine-v2-aggregation, mobile-responsive-design-system) —
  это коллизия из параллельной разработки. Перенумеровать и обновить
  cross-references — отдельной задачей, не блокирует 10.9a.
