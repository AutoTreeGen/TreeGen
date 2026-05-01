# Voice-to-tree — runbook

> Phase 10.9a operational runbook. Контекст — ADR-0064 + spec
> `docs/feature_voice_to_tree.md`. Этот документ — операционный
> (что нажимать, на что реагировать); ADR описывает **почему**.

---

## Архитектура (high-level)

Owner записывает голос → audio в GCS → arq job → Whisper API → транскрипт
в Postgres → review queue → коммит в дерево.

Подробности:

- ADR-0064 — выбор провайдера, consent scope, retention, error semantics.
- `docs/feature_voice_to_tree.md` — data model `audio_sessions`,
  endpoint contracts, acceptance gate.
- ADR-0049 — erasure pipeline (используется при revoke consent).
- ADR-0043 — AI layer architecture (общие паттерны клиентов).

---

## Включение voice-to-tree для нового tree

Per-tree consent (НЕ global per-user — см. ADR-0064 §B). Пока хотя бы одно
дерево не дало явного consent, recorder заблокирован.

1. Owner идёт в `/trees/{tree_id}/voice`.
2. UI показывает consent banner с текстом из `docs/feature_voice_to_tree.md`
   §3.5 (RU + EN).
3. Owner кликает «I consent» → `POST /trees/{tree_id}/audio-consent`.
4. Backend ставит `tree_settings.consent_egress_at = now()`.
5. UI снимает disabled с кнопки Record.

**Defence-in-depth:** даже если фронт скомпрометирован,
`POST /audio-sessions` без `consent_egress_at` IS NOT NULL → `403
consent_required`. Это критический инвариант (см. §Privacy incident).

---

## Включение OPENAI_API_KEY на новом env

**Staging** — автоматизировано через `scripts/staging-deploy-10-9a.{ps1,sh}`:

```bash
export OPENAI_API_KEY="sk-..."
bash scripts/staging-deploy-10-9a.sh --project-id <gcp-project> --confirm
```

Скрипт сам создаст секрет и привяжет его к Cloud Run revision parser-service.

**Prod** — пока вручную (Phase 10.9c автоматизирует через CI):

1. `gcloud secrets create OPENAI_API_KEY --project=<prod> --replication-policy=automatic`
2. `printf '%s' "$OPENAI_API_KEY" | gcloud secrets versions add OPENAI_API_KEY --project=<prod> --data-file=-`
3. `gcloud run services update prod-parser-service --region=<region> --update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest`
4. Проверить, что `gcloud secrets versions access latest --secret=OPENAI_API_KEY --project=<prod>` возвращает ключ.
5. Triggernуть новую revision: `gcloud run services update prod-parser-service --update-labels=openai-rotated=$(date +%Y%m%d)`.

**Ротация ключа.** OpenAI key compromise → немедленно:

1. Revoke в OpenAI dashboard.
2. `gcloud secrets versions add OPENAI_API_KEY` с новым ключом.
3. `gcloud run services update --update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest` — pin'нёт latest, новая revision подхватит.
4. Постмортем: проверить логи на возможный leak (см. §Privacy incident).

**Dev** — ключ через `.env` (не коммитить). См. `.env.example`.

---

## Monitoring

Все метрики экспонируются parser-service'ом через Prometheus-endpoint
`/metrics`. В прод собираются Cloud Monitoring agent'ом (см. ADR-0043
§observability) и роутятся через `infrastructure/terraform/modules/monitoring/`.

| Метрика | Тип | Что считает | Алерт |
|---|---|---|---|
| `treegen_audio_sessions_total{status=...}` | counter | Создание сессий по статусам (`uploaded`, `transcribing`, `ready`, `failed`) | информативная |
| `treegen_audio_consent_revocations_total` | counter | Revoke consent по деревьям | информативная (баseline для оттока) |
| `treegen_audio_egress_without_consent_total` | counter | **Privacy incident** — попытка отправить аудио без `consent_egress_at` | **CRITICAL** — > 0 за 5 min |
| `treegen_whisper_cost_usd_daily` | gauge | Roll-up из Redis-телеметрии: сумма $ за прошлые 24 часа | warning при > $5/day |
| `treegen_whisper_latency_seconds` | histogram | end-to-end время от upload до ready | p95 > 30s — warning |

Алерты см. `infrastructure/terraform/environments/staging/audio_storage.tf`
и `modules/monitoring/main.tf`. Privacy alert добавлен в Phase 10.9a;
cost-rollup и latency — TODO к prod-rollout'у.

---

## Privacy incident response

Триггер: `treegen_audio_egress_without_consent_total > 0` за 5 минут.

**Это GDPR breach (Art. 33). 72-часовой clock запускается с момента, когда
incident detected.**

1. **Заблокировать endpoint feature-flag'ом** — `parser-service` env var
   `VOICE_TO_TREE_ENABLED=false`, новая revision Cloud Run:

   ```bash
   gcloud run services update prod-parser-service \
     --update-env-vars=VOICE_TO_TREE_ENABLED=false
   ```

2. **Аудит логов за инцидент-окно** — `gcloud logging read 'resource.type=cloud_run_revision
   resource.labels.service_name=prod-parser-service jsonPayload.event=audio_egress_attempt'
   --freshness=1h`. Идентифицировать affected `tree_id`.
3. **Уведомить affected user'а в течение 24 часов** (внутренний SLA — мягче
   GDPR 72h, но даёт буфер на постмортем). Шаблон email — `docs/templates/`
   (TBD, Phase 10.9c).
4. **Postmortem** в `docs/postmortems/` с root-cause + fix + регрессионным
   тестом (см. ADR-0008 на CI culture).
5. **GDPR notification** — если breach подтверждён (а не false-positive
   метрики), эскалировать к owner проекта для уведомления supervisory
   authority в течение 72 часов с момента detection.

**False-positive checklist** перед эскалацией:

- Это не тест с `consent_egress_at IS NULL` в integration suite?
- Метрика инкрементировалась из-за корректно отвергнутого запроса
  (т.е. backend поймал и вернул 403)? Тогда метрика плохо названа —
  переименовать в `treegen_audio_egress_blocked_total`. См. spec §3.5.

---

## Cost monitoring

Whisper Standard tier ≈ $0.006/min аудио. 30-секундная демо-запись ≈ $0.003.
Owner-only beta: ожидаемо < $0.10/day.

Daily roll-up:

```bash
# Stagingredis телеметрия → суточный agg, экспонируется как
# treegen_whisper_cost_usd_daily gauge
gcloud monitoring metrics list --filter='metric.type=~"treegen_whisper_cost_usd_daily"' --project=<prod>
```

Алерт > $5/day трактуется как:

- runaway бот / атака на consented endpoint;
- баг в retry-логике (Whisper 429 → loop);
- owner случайно загружает многочасовое аудио.

В каждом случае — открыть incident-issue, посмотреть `audio_sessions`
с `created_at > now() - interval '24h'`, оценить total `duration_seconds`.

---

## Erasure (revoke consent)

`DELETE /trees/{tree_id}/audio-consent` запускает async erasure через arq job
по паттерну ADR-0049:

1. Backend ставит `tree_settings.consent_egress_at = NULL`.
2. Backend enqueues `audio_erasure_job(tree_id)` в arq.
3. Job:
   a. Hard-delete всех `audio_sessions` этого tree из Postgres
      (НЕ soft-delete — это PII по GDPR Art. 9 паттерну DNA, см. ADR-0012).
   b. Удаляет audio-blob'ы из GCS bucket
      `${prefix}-staging-audio-sessions`.
   c. Затирает Redis-телеметрию по `tree_id`.
   d. Audit log: только `factum` события (`audio_consent_revoked_at`,
      `audio_sessions_erased_count`) — БЕЗ хешей файлов.
4. Owner получает email-confirmation в течение 24 часов.

**Что после erasure НЕ удаляется:**

- транскрипты, уже коммиченные в дерево (это derived facts, owner ими
  владеет — без revoke consent на сами факты);
- метрики counters (`treegen_audio_*_total`) — там нет PII, только
  агрегаты;
- Whisper-side кэш — OpenAI Standard tier хранит запросы 30 дней (см.
  ADR-0064 §F + spec §3.6). Это документировано в consent-тексте.

---

## Troubleshooting

| Симптом | Вероятная причина | Что проверить / сделать |
|---|---|---|
| Whisper 429 в логах | rate limit на OpenAI | подождать; если стабильно — апгрейд tier |
| `audio_sessions` стек в `transcribing` > 5 min | arq worker умер | `gcloud run services logs read prod-parser-service --filter=arq` |
| Транскрипт пустой | `AI_DRY_RUN=true` случайно прокинулся в env | `gcloud run services describe prod-parser-service --format='value(spec.template.spec.containers[0].env)'` |
| 403 `consent_required` сразу после grant | race между UI optimistic update и `tree_settings` flush | повторно нажать Record через 1–2 сек |
| Recorder не активируется | браузер блокирует mic permission | DevTools → Permissions → microphone = Allow; redeploy localhost |
| `OPENAI_API_KEY` not found в Cloud Run | секрет не привязан к revision | повторить `--update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest` |
| Bucket creation fails в terraform | bucket name globally taken | поменять `var.bucket_prefix` на уникальный с project-id |

---

## Ссылки

- ADR-0064 — voice-to-tree pipeline (`docs/adr/0064-voice-to-tree-pipeline.md`)
- ADR-0049 — erasure pipeline
- ADR-0043 — AI layer architecture
- ADR-0012 — DNA processing privacy (паттерн hard-delete для PII)
- ADR-0003 — versioning strategy (DNA / audio opt-out из soft-delete)
- spec — `docs/feature_voice_to_tree.md`
- demo rehearsal — `docs/runbooks/demo-rehearsal-2026-05-06.md`
- [GDPR Art. 9](https://gdpr-info.eu/art-9-gdpr/) — special categories (биометрия)
- [GDPR Art. 33](https://gdpr-info.eu/art-33-gdpr/) — breach notification (72h)
- [OpenAI Whisper API pricing](https://openai.com/api/pricing/) — $0.006/min Standard
