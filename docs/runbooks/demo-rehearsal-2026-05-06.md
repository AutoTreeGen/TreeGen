# Demo rehearsal — Phase 10.9a (06.05.2026)

> Operational checklist для демо voice-to-tree MVP. Контекст — ADR-0064 +
> `docs/feature_voice_to_tree.md` §3.7 (acceptance gate). Связанный runbook —
> `docs/runbooks/voice-to-tree.md`.

**Acceptance gate:** на staging записать 30-секундный RU-аудио, через ≤30 сек
видеть транскрипт. См. spec §3.7.

---

## За 24 часа до демо (05.05.2026)

Все шаги выполняются на staging. Если хоть один failed — escalate, демо НЕ
запускать. Plan-B (см. §«Если что-то сломалось») — крайняя мера.

### Deploy verification

- [ ] `bash scripts/staging-deploy-10-9a.sh --project-id <staging> --confirm` — exit 0
- [ ] Secret: `gcloud secrets versions access latest --secret=OPENAI_API_KEY --project=<staging>` возвращает ключ (не пустую строку)
- [ ] DB schema: `psql <staging-dsn> -c "\d audio_sessions"` показывает колонки `id`, `tree_id`, `status`, `consent_egress_at`, `audio_blob_uri`, `transcript_text`, `provenance`
- [ ] Bucket: `gsutil ls gs://<bucket-prefix>-staging-audio-sessions` — exit 0 (пустой ok)
- [ ] Cloud Run revision: `gcloud run services describe staging-parser-service --region=europe-west1 --format='value(status.latestReadyRevisionName)'` соответствует свежей revision (с label `phase=10-9a`)
- [ ] Migration: `uv run alembic current` (через staging DSN) показывает `0030_audio_sessions`

### Functional tests

- [ ] **Live recording happy path:**
  - Открыть Chrome → `https://staging-web-XXXX.run.app/trees/{demo-tree-id}/voice`
  - Grant consent → Record активируется
  - 30 sec RU тест: «тестовая запись о моём прадеде»
  - ≤ 30 sec → transcript появляется, содержит «прадед»
- [ ] **Consent gate (defence-in-depth):**
  - DevTools → Network → POST `/audio-sessions` без `Authorization` / без `consent_egress_at`
  - Ответ: HTTP 403 + `{"error_code": "consent_required"}`
- [ ] **Erasure smoke:**
  - Создать тестовую сессию для disposable дерева
  - DELETE `/trees/{disposable}/audio-consent` → arq job → audio-blob исчезает из GCS, ORM запись hard-deleted
- [ ] **Privacy alert sanity:** в Cloud Monitoring видна alert policy `Audio egress without consent (staging)` со статусом `OK`

### State snapshot

- [ ] `pg_dump --data-only -t persons -t families -t trees -t audio_sessions <staging-dsn> > demo-snapshot-2026-05-05.sql` — на случай если демо-tree случайно мутирует
- [ ] Snapshot отложен в личное хранилище (НЕ в репо — может содержать PII)

### Backup audio (Plan B)

- [ ] Pre-recorded синтетический RU-семейный нарратив (НЕ реальные ФИО предков owner'а), 30 сек, `.webm` в OPUS-codec
- [ ] Файл лежит в GCS: `gsutil cp demo-fallback.webm gs://<bucket>/demo-assets/`
- [ ] (НЕ коммитить аудио в репо — > 1MB и потенциальный PII)

---

## День демо (06.05.2026)

### За 30 минут до демо

- [ ] **Browser:** Chrome incognito, cache cleared, mic permission granted для staging-web origin
- [ ] **Background:** Slack/Telegram/Outlook закрыты (CPU + всплывающие уведомления)
- [ ] **Network:** speedtest upload ≥ 10 Mbps; если < 5 — переключиться на Plan B
- [ ] **Mic:** test recording 5 sec в OS settings, peak уровень видим
- [ ] **Backup:** pre-recorded `demo-fallback.webm` загружен в браузерную вкладку (drag-and-drop ready)
- [ ] **Tab discipline:** только staging-web, DevTools, и backup-вкладка. Остальное закрыть.

### Demo сценарий (3 минуты)

| Время | Действие | Что показать |
|---|---|---|
| 0:00–0:30 | Открыть `/trees/{demo-tree-id}/voice` | Consent banner с RU+EN текстом, кнопка Record disabled |
| 0:30–0:40 | Grant consent | Кнопка Record активируется (видимый visual feedback) |
| 0:40–1:40 | Record 60 sec | Текст: «Мой прадед Иван родился в 1850 году в Минске. У него была жена Сара. Они переехали в Одессу в 1880 году.» |
| 1:40–2:10 | Wait for transcript | Spinner → transcript appears (≤ 30 sec) |
| 2:10–2:40 | Зачитать транскрипт | Подсветить «прадед», «1850», «Минск», «Сара», «Одесса» |
| 2:40–3:00 | Closing | «Дальше LLM извлечёт person/event candidates → review queue (Phase 10.9b)» |

### Если что-то сломалось

| Симптом | Plan B |
|---|---|
| Whisper timeout (> 30 sec spinner) | Перезапустить запись 1 раз. Если опять — switch to backup audio: загрузить `demo-fallback.webm` через `<input type=file>` в DevTools |
| HTTP 403 `consent_required` после grant | Перезагрузить страницу, повторно grant consent (race с `tree_settings` flush, см. troubleshooting в `voice-to-tree.md`) |
| Transcript empty | DevTools → Console → искать `AI_DRY_RUN=true`. Если так — incident, escalate; на демо переключиться на pre-recorded скриншот транскрипта |
| /healthz парсера не отвечает | `gcloud run services logs tail staging-parser-service --region=europe-west1` в боковом терминале; если pod stuck — `gcloud run services update --update-labels=demo-restart=$(date +%s)` |
| Mic permission не дала | Закрыть demo-вкладку, открыть `chrome://settings/content/microphone`, добавить staging-origin → reload |
| Network упал | Pre-recorded audio + слайд «архитектура voice-to-tree pipeline» (заранее экспортирован из spec §2) |

### После демо

- [ ] Удалить тестовое аудио из demo-tree через UI (если не коммитили в дерево как факты)
- [ ] `gcloud logging read 'resource.labels.service_name=staging-parser-service' --freshness=1h --limit=200` → проверить нет `audio_egress_attempt` без consent
- [ ] Метрика `treegen_audio_sessions_total{status="ready"}` инкрементировалась минимум на 1 (демо успешно прошло pipeline)
- [ ] `treegen_audio_egress_without_consent_total` НЕ изменилась (privacy инвариант не нарушен)

---

## Ссылки

- spec — `docs/feature_voice_to_tree.md` §3.7 acceptance criteria
- ADR-0064 — `docs/adr/0064-voice-to-tree-pipeline.md`
- ops runbook — `docs/runbooks/voice-to-tree.md` (troubleshooting + privacy incident)
- deploy script — `scripts/staging-deploy-10-9a.{ps1,sh}`
