# ADR-0032: Secrets management

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `infra`, `security`, `gcp`, `phase-13`

## Контекст

Phase 13.0 поднял Secret Manager-контейнеры через терраформ-модуль `secrets`,
но не зафиксировал политику: где живут секреты, как их выдают сервисам, как
ротируют, что делать в инциденте, и почему DNA-Fernet-ключ нельзя ротировать
как обычный API-ключ.

Phase 13.1 закрывает этот пробел и одновременно ликвидирует последний
long-lived JSON-ключ (CI deploy SA) — заменяет его на Workload Identity
Federation. Без явной политики каждый новый секрет ставит вопрос «куда
класть и кто получает доступ» с нуля.

## Рассмотренные варианты

### A. Один backend (Google Secret Manager) для всех окружений

- ✅ Один источник правды, один tooling, один audit log.
- ✅ IAM на per-secret уровне (granular accessor bindings).
- ✅ Auto-replication, KMS encryption at rest.
- ❌ Локальная разработка зависит от gcloud auth — медленнее dev-loop.

### B. Вспомогательный `.env` локально, Secret Manager в облаке

- ✅ Локальный dev — мгновенный, без сети.
- ✅ Прод-поверхность не меняется.
- ❌ Дрейф между `.env.example` и набором GCP-секретов — лечится документацией
  и проверками в pre-commit (см. §Sync).

### C. HashiCorp Vault

- ✅ Cloud-agnostic, мощный.
- ❌ +1 инфраструктурный компонент для соло-разработчика. Operational tax
  превышает выгоду на нашем масштабе.

### D. CI auth: long-lived JSON key vs Workload Identity Federation

- JSON key: ✅ просто, ❌ долгоживущий, ❌ хранится в repo secret, ❌ ротация
  редко делается.
- WIF: ✅ короткоживущие OIDC-токены, ✅ привязка к репо+ref на стороне GCP,
  ✅ нечего ротировать, ❌ +1 шаг bootstrap.

## Решение

- **Backend:** Google Secret Manager в проде/staging, `.env` (gitignored)
  локально — Вариант B.
- **CI auth:** Workload Identity Federation only. JSON ключи запрещены —
  модуль `gha-oidc` ставит pool/provider/SA, deploy-staging.yml аутентифицируется
  через `google-github-actions/auth@v2` с `workload_identity_provider`.
- **Granularity:** один Secret Manager-secret на одну смысловую сущность
  (БД-пароль, FS client secret, и т.д.). Не паковать в JSON-блобы.
- **IAM:** secretAccessor выдаётся per-SA per-secret, не на проект целиком
  (исключение — `github-deployer` SA, см. §CI).
- **Rotation:** разные политики для разных классов секретов (см. §Inventory).

## Inventory

| Секрет | Назначение | Storage | Кто читает | Ротация | Особенности |
|---|---|---|---|---|---|
| `staging-db-password` | пароль AlloyDB Omni приложения | Secret Manager + cloud-init на VM | parser-service, dna-service, notification-service runtime SA + AlloyDB-Omni VM SA | 90 дней | Ротация = новая версия + restart Cloud Run + `ALTER USER` на VM |
| `staging-anthropic-api-key` | Claude API key | Secret Manager | parser-service runtime SA | 90 дней | Ротация — через Anthropic console, бесшовно |
| `staging-fs-client-id` | FamilySearch app key | Secret Manager | parser-service runtime SA | по событию (compromise, app re-registration) | Не критично PII; можно перевыпустить через FS dev console |
| `staging-fs-client-secret` | FamilySearch OAuth client secret | Secret Manager | parser-service runtime SA | 90 дней | После ротации FS невалидирует old client_secret серверной частью OAuth |
| `staging-fs-token-key` | Fernet-key для шифрования FS OAuth токенов в БД (см. ADR-0027) | Secret Manager | parser-service runtime SA | **никогда** автоматически | Перешифровать ВСЕ строки в `users.fs_token_encrypted` — это миграция, не ротация. См. §Fernet |
| `staging-encryption-key` | Fernet-key для envelope-encryption DNA-сегментов | Secret Manager | dna-service runtime SA | **никогда** автоматически | То же что выше; см. §Fernet |
| `staging-jwt-signing-key` _(planned, Phase 4.2)_ | подпись session JWT | Secret Manager | parser-service runtime SA | 30 дней | Поддержать N-1 версию параллельно для grace-period verify-only |
| `local .env` | дев-копии всех ключей выше | filesystem | разработчик | по compromise | В .gitignore. `.env.example` коммитится без значений |

## Rotation policy

### Стандартная ротация (90 дней, для API-ключей)

```bash
# 1. Сгенерировать/получить новое значение из upstream (Anthropic, FS, ...).
# 2. Положить как новую версию того же секрета.
printf '%s' "$NEW_VALUE" | gcloud secrets versions add staging-anthropic-api-key --data-file=-

# 3. Cloud Run подхватывает `latest` автоматически — но новые revisions поднимаются
#    только при следующем deploy. Чтобы применить сразу:
gcloud run services update staging-parser-service \
  --region=europe-west1 \
  --update-env-vars=_ROTATION=$(date +%s)

# 4. После того как новая версия проверена в проде — отключить старую:
gcloud secrets versions disable staging-anthropic-api-key --version=N-1
# (НЕ destroy — disable обратимо в течение 30 дней)

# 5. Через 30 дней:
gcloud secrets versions destroy staging-anthropic-api-key --version=N-1
```

### Pinned versions vs `latest`

Терраформный `services` модуль ссылается на `version = "latest"` — это
осознанный выбор: ротация = одна команда, без re-apply Терраформа.
Trade-off: bad rotation roll-forward'ится без явного code-review. Для
особо критичных секретов (jwt-signing-key, когда появится) — сделаем
pin к конкретной версии и потребуем re-apply. На текущей фазе цена
ошибки приемлема (откат = `gcloud secrets versions destroy`).

## Break-glass procedure

**Утечка одного секрета:**

1. Немедленно сгенерировать новую версию через `gcloud secrets versions add`.
2. Disabled старую: `gcloud secrets versions disable ... --version=N-1`.
3. Force-redeploy всех потребителей (`gcloud run services update ... --update-env-vars=_BREAKGLASS=$(date +%s)`).
4. Проверить Cloud Audit Logs (`logName="projects/PROJECT/logs/cloudaudit.googleapis.com%2Fdata_access"`)
   на любые `AccessSecretVersion` запросы между утечкой и disable —
   зафиксировать в инцидент-логе.
5. Если утекли FS / Anthropic ключи — также инвалидировать на стороне provider'а
   (FS dev console, Anthropic console).

**Утечка Fernet-ключа (DNA / FS-token):** см. §Fernet ниже — это не
ротация, а миграция данных.

**Компрометация WIF:** удалить principal'ы из `github-deployer` SA, выкинуть
скомпрометированный workflow run из истории, проверить Audit Logs за окно
`token issued` → `now` на любые подозрительные действия. Pool/provider можно
пересоздать через `terraform taint module.gha_oidc.google_iam_workload_identity_pool.gha`.

## Fernet keys — special handling

Fernet-ключи (`staging-fs-token-key`, `staging-encryption-key`) — это
**ключи шифрования данных at-rest**, не credentials. Если сменить ключ
без re-encrypt — все старые токены / DNA-сегменты в БД станут unreadable.

### Ограничения

- **Не ротируем по календарю.** Ротация = миграция: дешифровать всё
  старым ключом, зашифровать новым, обновить строки. Это offline-операция
  (минуты-часы простоя, в зависимости от объёма).
- **Не disable старую версию пока не доказано, что все данные перезашифрованы.**
  Cloud Run runtime достаёт `latest` — но пока в БД остались строки,
  зашифрованные предыдущей версией, обе должны быть accessible. Нужен
  multi-version reader на app-стороне (см. ниже).
- **Backup strategy.** Backup БД без backup'а ключа = brick. Ключи бэкапим
  отдельно (offline, encrypted USB / paper) с момента генерации.

### Trigger'ы ротации (только эти)

1. Утечка: WIF compromised, Secret Manager IAM misconfig, lost laptop с
   `.env`.
2. Алгоритмический deprecation (Fernet → AES-GCM-SIV когда), запланированный
   через ADR.
3. Регуляторный (GDPR DPA пересматривается, регулятор требует rekey).

### Migration playbook

Шаги — в `docs/runbooks/fernet-rekey.md` (ещё не написан, TODO Phase 13.1.x):

1. Включить read-only режим на сервис (rate-limit / 503).
2. Двойной reader: добавить env `OLD_FERNET_KEY` (предыдущая версия), код
   пробует new, fallback на old.
3. Online migration script: `UPDATE users SET fs_token_encrypted = reencrypt(...)`
   батчами, при ошибке — rollback версии.
4. Снять OLD_FERNET_KEY env, deploy.
5. `gcloud secrets versions disable` — старая версия.
6. Через 30 дней — `destroy`.

Без этого playbook'а — не запускать ротацию. Потеря ключа = потеря данных.

## Sync between local `.env.example` and GCP secrets

Дрейф (новый секрет добавили в код, но забыли в один из двух мест) ловится
вручную. Phase 13.2 TODO: pre-commit-хук, который парсит `Settings`-классы
сервисов и сверяет с обоими списками. Сейчас — code review.

## CI auth

GitHub Actions deploy-staging.yml использует Workload Identity Federation
вместо JSON-ключа:

- Pool: `staging-gha`, provider `github-staging`. Issuer: token.actions.githubusercontent.com.
- Provider attribute condition: `assertion.repository == "AutoTreeGen/TreeGen"` —
  только этот репо может exchange OIDC token.
- IAM principal на SA `staging-github-deployer` ограничен ref'ом
  `refs/heads/main` через `allowed_refs` (по умолчанию). Feature-branches
  не получают deploy-rights.
- Роли SA: `roles/{run.admin, artifactregistry.writer, cloudsql.client,
  secretmanager.secretAccessor, iam.serviceAccountUser}` — минимум для
  CI deploy. Никаких Owner / Editor.
- Никаких JSON-ключей в repo secrets. GitHub repo _variables_ (не secrets!)
  хранят non-sensitive значения: `GCP_WORKLOAD_IDENTITY_PROVIDER`,
  `GCP_DEPLOY_SA_EMAIL`. Это публичные resource names, не credentials.

См. `infrastructure/terraform/modules/gha-oidc/`.

## Последствия

- ✅ Каждый секрет имеет владельца, политику ротации и playbook на инцидент.
- ✅ Long-lived JSON keys выкорчеваны — рота­ция WIF не нужна, нет «забытых»
  ключей с прав Editor.
- ✅ Fernet-ключи защищены явно — никто случайно не сделает «`gcloud secrets
  versions destroy`» по календарной триггерной системе.
- ❌ Ротация требует ручного шага (force-redeploy). Можно автоматизировать
  через Cloud Scheduler + Cloud Function — отложено до Phase 13.2.
- ❌ Drift `.env`/`.env.example`/Secret Manager не валидируется
  автоматически. Pre-commit-хук — TODO.

## Когда пересмотреть

- Появляется ≥ 3 человек в команде → нужен Vault-уровень audit + RBAC.
- Регуляторное требование «secret rotation < 30d» → автоматизация.
- Cross-cloud deployment (AWS Secrets Manager?) → cloud-agnostic backend (Vault).
- DNA-объём перевалит за 1 ТБ → re-key migration больше не помещается в окно
  обслуживания → нужен online-перешифровщик с двойным reader'ом из коробки.

## Ссылки

- ADR-0027 — Fernet at-rest для FS OAuth-токенов.
- ADR-0031 — GCP deployment architecture.
- `infrastructure/terraform/modules/secrets/` — терраформный код.
- `infrastructure/terraform/modules/gha-oidc/` — WIF-настройка.
- [Google Secret Manager docs](https://cloud.google.com/secret-manager/docs)
- [Workload Identity Federation for GitHub Actions](https://cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)
