# Agent #6 — Phase 10.9a: infra/staging prep + ROADMAP update + runbooks

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (RU comments, EN identifiers, Conventional Commits,
   pre-commit must pass, **`--no-verify` запрещён**).
2. `docs/feature_voice_to_tree.md` — §3.5 consent gate, §3.6 retention, §3.7
   acceptance.
3. `docs/adr/0064-voice-to-tree-pipeline.md` — §«Решение» полностью + §Риски.
4. `ROADMAP.md` — §14.1 (AI use cases) — на 01.05 содержит 6 пунктов;
   voice-to-tree предлагается добавить как пункт 7.
5. Existing infra patterns:
   - `infrastructure/terraform/` — terraform manifests (если есть)
   - `infrastructure/k8s/` — k8s deployment manifests (если есть)
   - `docker compose.yml` (корень) — local dev MinIO setup
   - `infrastructure/postgres/init/` — DB init scripts
6. Existing runbooks: `docs/runbooks/` (если есть). Если нет — создаёшь
   первый по этому шаблону.

## Задача

Подготовить **всё, что НЕ app-code** для Phase 10.9a, чтобы:

1. Когда #1-#4 смерджатся, на staging уже было место для деплоя.
2. Команда демо 06.05 имела чёткий runbook с командами «что нажимать».
3. ROADMAP стал source-of-truth с правильным upmention.
4. Privacy-инцидент (egress без consent) детектировался автоматически.

**Парам параллелится с #1-#4** — твой scope не пересекается с app-code'ом.

## Branch

```text
chore/phase-10-9a-staging-prep
```

От свежего main: `git checkout main && git pull && git checkout -b chore/phase-10-9a-staging-prep`.

## Scope

### A. ROADMAP.md update

`ROADMAP.md` §14.1 — найди существующий нумерованный список из 6 use cases
(document analyzer, translator, research assistant, hypothesis explainer,
document summarizer, vector search). Добавь:

```markdown
7. **Voice-to-tree:** owner записывает голосом семейные истории →
   Whisper-транскрипт → LLM-extraction person/event candidates →
   review queue → коммит в дерево. См. ADR-0064 + `docs/feature_voice_to_tree.md`.
   Demo MVP 06.05.2026.
```

Также обнови §22 «Метрики успеха» (если есть пункт про AI-расходы) —
добавь воcs-cost под Phase 10.9.

### B. Terraform / k8s — staging GCS bucket для аудио

Проверь, существует ли `infrastructure/terraform/` или эквивалент.

Если **terraform**:

`infrastructure/terraform/staging/audio_storage.tf`:

```hcl
resource "google_storage_bucket" "audio_sessions_staging" {
  name          = "treegen-audio-sessions-staging"
  location      = "us-central1"
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 365  # 1 год — TTL retention; coordination с GDPR-policy
    }
    action {
      type = "Delete"
    }
  }

  versioning { enabled = false }   # аудио не versionируется

  labels = {
    phase   = "10-9a"
    purpose = "voice-to-tree-audio"
    pii     = "yes"
  }
}
```

Если **k8s manifests без terraform** — добавь `ConfigMap` со ссылкой на
bucket, бaket создаётся через gcloud-script (см. §C).

Если **ничего из того нет** (early-stage project) — STOP, не плоди инфра-сlojnost'i;
просто создай runbook (§D) с инструкцией ручного создания bucket'а.

### C. Helper scripts

`scripts/staging-deploy-10-9a.ps1` (PowerShell):

```powershell
<#
.SYNOPSIS
Развернуть Phase 10.9a на staging-кластере.

.DESCRIPTION
Применить alembic-миграцию 0030, создать GCS bucket, выставить OPENAI_API_KEY
в Secret Manager, перекатить parser-service deployment.

.PREREQUISITES
- gcloud authenticated на staging project
- kubectl context = staging
- OPENAI_API_KEY в env-переменной (не в скрипте — в env)

.EXAMPLE
$env:OPENAI_API_KEY = "sk-..."
./scripts/staging-deploy-10-9a.ps1 -DryRun
./scripts/staging-deploy-10-9a.ps1 -Confirm
#>

param(
    [switch]$DryRun,
    [switch]$Confirm
)

# 1. Проверка prereq'ов
# 2. gcloud secrets create OPENAI_API_KEY --replication-policy=automatic ...
# 3. gsutil mb -l us-central1 gs://treegen-audio-sessions-staging
# 4. kubectl set env deployment/parser-service OPENAI_API_KEY=...
# 5. uv run alembic upgrade head (на staging-DB)
# 6. kubectl rollout restart deployment/parser-service
# 7. Smoke-test: curl POST /health → 200
```

Полный скрипт с Write-Host status, error handling, и `-WhatIf` поддержкой.

`scripts/staging-deploy-10-9a.sh` (bash) — паритет с PS1 (см. CLAUDE.md
про паритет check.sh/check.ps1).

### D. Runbook — `docs/runbooks/voice-to-tree.md`

Operational runbook со следующими разделами:

```markdown
# Runbook — Voice-to-tree (Phase 10.9a)

## Архитектура (link → ADR-0064 + spec)

## Включение для нового tree

1. Owner идёт в /trees/{id}/voice
2. Кликает «I consent»
3. Backend ставит `consent_egress_at = now()` в `tree_settings`
4. Recorder активируется

## Включение OPENAI_API_KEY на новом env

(prod / staging / dev — пошагово)

## Monitoring

- Metric `treegen_audio_sessions_total{status=...}` — счётчик
- Metric `treegen_audio_consent_revocations_total` — отзывы
- Metric `treegen_audio_egress_without_consent_total` — **CRITICAL alert,
  trigger на > 0 за 5min** (это privacy incident)
- Metric `treegen_whisper_cost_usd_daily` — daily cost rollup, alert на
  > $5/day

## Privacy incident response

(если `treegen_audio_egress_without_consent_total > 0`)

1. Заблокировать parser-service POST /audio-sessions endpoint feature-flag'ом
2. Аудит логов за инцидент-окно
3. Уведомить affected user'а в течение 24 часов (GDPR Art. 33)
4. Postmortem с root-cause и fix

## Cost monitoring

(daily rollup из Redis-телеметрии)

## Erasure (revoke consent)

(пошагово что происходит)

## Troubleshooting

- Whisper 429 → rate limit, ждать
- audio_sessions stuck в transcribing → arq worker died, kubectl get pods
- transcript empty → AI_DRY_RUN включён случайно
```

### E. Demo rehearsal script — `docs/runbooks/demo-rehearsal-2026-05-06.md`

Пошаговый скрипт для демо 06.05:

```markdown
# Demo rehearsal — Phase 10.9a (06.05.2026)

## За 24 часа до демо (05.05)

- [ ] staging-deploy-10-9a.ps1 -Confirm — passing
- [ ] OPENAI_API_KEY в Secret Manager — verified `gcloud secrets versions access latest`
- [ ] audio_sessions table — `psql -c "\d audio_sessions"` показывает все поля
- [ ] MinIO/GCS bucket — `gsutil ls gs://treegen-audio-sessions-staging`
- [ ] Test recording: open Chrome → /trees/{demo-tree-id}/voice → grant consent →
      record 30 sec RU («тестовая запись о моём прадеде») → wait ≤ 30 sec →
      verify transcript text contains «прадед»
- [ ] Test 403: open DevTools → POST /audio-sessions без consent header →
      ожидаем 403 с error_code=consent_required
- [ ] Backup demo-tree state: `pg_dump -t persons -t families -t trees ... > demo-snapshot.sql`

## День демо (06.05)

### За 30 минут до демо

- [ ] Browser cache cleared
- [ ] Microphone permissions granted
- [ ] Background apps closed (CPU)
- [ ] Network speed test ≥ 10 Mbps upload
- [ ] Backup audio file pre-recorded (если live запись провалится)

### Сценарий (3 минуты)

1. (30 сек) Открыть /trees/{demo-tree-id}/voice — показать consent banner
2. (10 сек) Grant consent — кнопка Record активируется
3. (60 сек) Record: «Мой прадед Иван родился в 1850 году в Минске. У него
   была жена Сара. Они переехали в Одессу в 1880 году.»
4. (30 сек) Wait for transcript — показать spinner → transcript appears
5. (30 сек) Зачитать transcript, обсудить «дальше LLM извлечёт персон, в
   следующей версии»

### Если что-то сломалось

- Whisper timeout → перезапустить запись (Plan B: показать pre-recorded)
- 403 consent_required → owner забыл grant — кликнуть повторно
- transcript empty → check console, если AI_DRY_RUN — disable, redeploy
```

### F. Privacy alert rule

Если в проекте есть Prometheus / OpenTelemetry — добавь rule:

`infrastructure/monitoring/audio-privacy-alert.yaml` (или в существующий
alerting config):

```yaml
groups:
  - name: voice-to-tree-privacy
    interval: 1m
    rules:
      - alert: AudioEgressWithoutConsent
        expr: increase(treegen_audio_egress_without_consent_total[5m]) > 0
        for: 0s   # immediate page
        labels:
          severity: critical
          phase: "10-9a"
          category: privacy
        annotations:
          summary: "Audio egressed without consent — privacy incident"
          runbook_url: "docs/runbooks/voice-to-tree.md#privacy-incident-response"
```

Если monitoring infra ещё не настроена — отметь в runbook'е TODO для прод-rollout'а.

## Definition of Done

- [ ] §A: ROADMAP.md §14.1 включает voice-to-tree как пункт 7
- [ ] §B: terraform/k8s manifest для staging-bucket (ИЛИ runbook-инструкция
      если infra-as-code ещё нет)
- [ ] §C: 2 deploy-скрипта (PS1 + bash) с паритетом
- [ ] §D: `docs/runbooks/voice-to-tree.md` написан
- [ ] §E: `docs/runbooks/demo-rehearsal-2026-05-06.md` написан
- [ ] §F: prometheus alert rule (или runbook TODO)
- [ ] `uv run pre-commit run --all-files` — passing (не должен ломать,
      только новые файлы)
- [ ] PR-описание перечисляет всё A-F и явно говорит «независимо от #1-#4,
      не блокирует их merge»

## Что НЕ трогать

- `packages/`, `services/`, `apps/web/src/` — зоны #1-#4
- `infrastructure/alembic/versions/` — закрыто #1
- `.env.example` — закрыто #3 (не дублируй)
- `apps/landing/`, `docs/adr/0003-*`, `docs/adr/0057-*`, `scripts/*.ps1`
  (existing) — закрыто #5

## Подводные камни

1. **Если terraform нет в проекте** — не вводи новый инструмент. Используй
   gcloud CLI в helper-скрипте + runbook.
2. **PowerShell + bash паритет.** `scripts/staging-deploy-10-9a.ps1`
   и `.sh` должны делать **одно и то же**. Это требование CLAUDE.md
   (`tests/test_ci_parity.py` паттерн).
3. **OPENAI_API_KEY никогда в скрипте.** Только из `$env:OPENAI_API_KEY`
   или Secret Manager. Никаких хардкод-fallback'ов.
4. **gsutil syntax.** Bucket name должен быть unique globally;
   `treegen-audio-sessions-staging` может быть занят — fallback на
   `treegen-{project-id}-audio-sessions-staging`.
5. **Demo Plan B.** Pre-record demo audio (не реальные ФИО предков owner'а
   — синтетика). Лежит в `docs/runbooks/assets/demo-fallback.webm`. Не
   коммитить аудио в репо если > 1MB — ссылка на GCS.

## Conventional Commits шаблоны

```text
docs(roadmap): add voice-to-tree as AI use case 7 (Phase 10.9 reference)
chore(infra): add staging GCS bucket for audio sessions (Phase 10.9a)
chore(scripts): add staging-deploy-10-9a helper (PS1 + bash parity)
docs(runbook): add voice-to-tree operational runbook
docs(runbook): add demo-rehearsal-2026-05-06 script
chore(monitoring): add AudioEgressWithoutConsent privacy alert rule
```
