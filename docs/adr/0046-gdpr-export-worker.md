# ADR-0046: GDPR data export worker (Phase 4.11a)

- **Status:** Accepted
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `gdpr`, `privacy`, `worker`, `storage`, `audit`

## Контекст

GDPR Article 15 (right of access) и Article 20 (right of data portability)
обязывают предоставить пользователю его данные в machine-readable формате
по запросу. Phase 4.10b добавила UI и stub-эндпоинт `POST /users/me/export-request`,
который вставляет `user_action_requests` row со `status='pending'`. Phase 4.11a
должна:

1. Реально обработать pending-row: собрать данные, сериализовать,
   сложить в storage с короткоживущим signed-URL'ом, оповестить email'ом.
2. Дать пользователю canonical способ получить fresh signed-URL
   повторно (15 мин TTL означает что email-link протухает быстро).
3. Зафиксировать каждый GDPR-action в audit-trail для compliance
   (Art. 30 — record of processing activities).

Конкретные силы:

- **Privacy hard rule (CLAUDE.md §3.5):** DNA-данные — special category;
  worker НЕ имеет ключей расшифровки (encrypt-at-rest на application
  level), так что plaintext DNA-сегменты в export физически невозможны.
- **Существующий audit-механизм** (`shared_models.audit`) — listener,
  привязанный к `tree_id`. User-level GDPR actions не относятся к
  конкретному дереву.
- **Multi-tenant isolation:** export строго одного user'а не должен
  утечь данные shared-tree members'ов.
- **Storage в проде — GCS, локально — MinIO, в тестах — InMemory.** Нужна
  абстракция, не хардкод.
- **Cursor pagination:** запрошена owner'ом (consistency с Phase 8.0
  notifications wording, хотя на момент написания notifications.py
  использует offset+limit; cursor лучше для long-tail history если
  у пользователя со временем накопится много request'ов).

## Рассмотренные варианты

### Storage layer

#### Вариант A — отдельный helper в parser-service, без abstraction

- ✅ Минимум кода; быстро.
- ❌ DRY-violation с уже существующим `dna-service.services.storage`.
- ❌ Нельзя тестировать через InMemory без monkey-patch'а boto3.
- ❌ Production swap GCS → S3 = переписать всё.

#### Вариант B — Protocol + 3 backends в shared-models (выбран)

- ✅ Реализации (MinIO/S3, GCS, InMemory) под одним интерфейсом.
- ✅ Lazy imports тяжёлых SDK'ов через optional extras
  (`storage-minio` / `storage-gcs`) — dev-окружение остаётся тонким.
- ✅ Тестам достаточно `InMemoryStorage()` — никаких docker'ов.
- ❌ Дополнительный 350-line модуль; нужно поддерживать 3 backends.

#### Вариант C — `packages/object-storage` как отдельный workspace package

- ✅ Самый чистый long-term shape; dna-service может промоутить свой
  storage сюда.
- ❌ Новый пакет = workspace-mod + мульти-PR refactor + обновление
  CI parity. Несоразмерно одной фазе.

### Audit log shape

#### Вариант A — отдельная таблица `user_audit_log`

- ✅ Чистая семантическая изоляция.
- ❌ Дублирует структуру и индексы; UI для admin'а / GDPR-officer'а
  должен джойнить две таблицы.
- ❌ Migration footprint.

#### Вариант B — REUSE audit_log с nullable tree_id (выбран)

- ✅ Один источник правды; queries и UI не меняются.
- ✅ Auto-listener (`shared_models.audit._make_audit_entry`) уже
  отфильтровывает объекты без `tree_id` — значит, добавить nullable
  безопасно для существующего auto-audit pipeline.
- ❌ Lossy downgrade миграции (нужно `DELETE FROM audit_log WHERE
  tree_id IS NULL` для возврата к NOT NULL).
- ❌ `action` колонку пришлось расширить с `varchar(16)` до `varchar(32)` —
  `"export_processing"` (17) не помещался.

#### Вариант C — структурный logger + user_action_requests как audit

- ✅ Никаких schema-изменений; `processed_at` + `error` + `request_metadata`
  уже фиксируют lifecycle.
- ❌ Нет single-source-of-truth для GDPR-officer'а (Art. 30).
- ❌ Logger-payload не запрашивается через query — нужно scrape stdout.

### Pagination

#### Вариант A — offset+limit (как notifications.py)

- ✅ Consistency с существующим кодом.
- ❌ Owner попросил cursor; offset страдает от skip-при-вставке (хотя
  для одного user'а вставки — десятки раз в год).

#### Вариант B — cursor on `(created_at DESC, id DESC)` (выбран)

- ✅ Owner-requested. Stable под concurrent inserts.
- ✅ Opaque base64 token — caller не парсит, просто пересылает обратно.
- ❌ Чуть больше кода (encode/decode + 422 на invalid).

### Auto-retry

- Worker `run_user_export_job` запускается **без auto-retry**. GDPR-export
  тяжёлый (multi-MB ZIP, network round-trips к storage и email-service);
  silent-retry рискует duplicate-email, double-charged storage put'ы и
  user-видимый «status flapping». Failure → user видит `status='failed'`
  с `error` в `GET /users/me/requests` и решает retry вручную через
  новый `POST /users/me/export-request` (после 30-дневного cooldown'а
  на storage object).

## Решение

Выбраны: **Storage Variant B**, **Audit Variant B**, **Pagination Variant B**,
**Manual-retry only**.

Конкретно:

1. `shared_models.storage` — Protocol + `MinIOStorage`/`GCSStorage`/`InMemoryStorage`.
   Default-env `STORAGE_BACKEND=minio`. Lazy-imports SDK'ов.
2. Migration `0021_audit_log_user_actions`:
   - `audit_log.tree_id` → nullable.
   - `audit_log.action` → `varchar(32)`.
   - Партиал-индекс `ix_audit_log_user_actions ON
     (actor_user_id, action, created_at) WHERE tree_id IS NULL`.
3. `AuditAction` enum: `EXPORT_REQUESTED`, `EXPORT_PROCESSING`,
   `EXPORT_COMPLETED`, `EXPORT_FAILED`, `ERASURE_REQUESTED`.
4. `EmailKind.EXPORT_READY` + Jinja2 templates (`en` / `ru`).
5. `parser_service.services.user_export_runner`:
   - Loads `UserActionRequest` row.
   - Idempotent: terminal states → no-op return.
   - Collects: profile, owned trees + nested entities, DNA metadata
     (без segments), audit-log entries (where `actor_user_id == user_id`),
     own action_requests history, memberships.
   - Serializes to ZIP (`manifest.json` + per-category JSON files).
   - Uploads to `gdpr-exports/{user_id}/{request_id}.zip`.
   - Issues 15-min signed URL → `send_transactional_email("export_ready", ...)`.
   - Updates row to `status='done'` + audit `EXPORT_COMPLETED`.
   - Failure: `status='failed'` + `error` + audit `EXPORT_FAILED`.
6. `parser_service.api.users.list_my_requests` теперь cursor-paginated
   с filter'ами `kind` / `status`. Для каждого `done` export'а
   запрашивает fresh signed URL у storage.
7. `POST /users/me/export-request` enqueue'ит `run_user_export_job` сразу
   после commit'а row.

### TTL values

- **15 мин для signed URL** — короткое окно ограничивает blast-radius
  email-forwarding'а или скриншот-leak'а. List endpoint всегда отдаёт
  fresh URL, так что UX не страдает.
- **30 дней для object retention** — отраслевой baseline для GDPR
  exports (баланс между Art. 17 right-to-erasure pressure и Art. 20
  practical access window). Реализовано через bucket lifecycle policy
  на storage-side, не из application code.

### Manifest format (zip_v1)

```text
manifest.json                # version, generated_at, file index, exclusions
profile.json                 # users-row sans secrets
trees/<tree_id>.json         # owned trees (one file each), все nested entities
dna/kits.json                # DnaKit metadata
dna/test_records.json        # DnaTestRecord metadata (NO blob bytes)
dna/consents.json
dna/imports.json
dna/matches.json             # NO segment data (special category)
audit_log.json               # entries where actor_user_id == user_id
action_requests.json
memberships.json
```

### Что НЕ включается в export (документировано в `manifest.excluded`)

1. **Encrypted DNA segment blobs.** Worker не имеет per-user decryption
   key; ciphertext без key бесполезен и его передача потенциально вводит
   user'а в заблуждение («да, мои данные у них» когда они unrecoverable).
2. **OAuth tokens** (`users.fs_token_encrypted`) — secret, не personal-data.
3. **Internal auth identifiers** (`external_auth_id`, `clerk_user_id`) —
   internal-system-state, не user-facing data.
4. **Audit-log entries от других actor'ов** в shared trees — это о тех
   user'ах, не о текущем.

## Последствия

### Положительные

- GDPR Art. 15/20 compliance demonstrable: пользователь нажимает кнопку,
  получает email с ссылкой, скачивает архив со всеми своими данными.
- Audit-trail (`audit_log` rows) — single-source-of-truth для compliance
  inquiry; queryable одним select'ом по `actor_user_id` + `action LIKE 'export_%'`.
- Storage abstraction готова к будущим use-case'ам (DNA encrypted blobs
  могут переехать сюда же — Phase 6.x).
- Cursor pagination не сломается на большом history (если у user'а
  100+ request'ов).

### Отрицательные / стоимость

- Migration 0021 lossy на downgrade (DELETE FROM audit_log WHERE
  tree_id IS NULL). Acceptable trade-off; downgrade на проде = bug-fix
  scenario, не routine.
- Дополнительные deps: `boto3` (~15 МБ wheels) или `google-cloud-storage`
  (~30 МБ) — изолированы через optional extras.
- 700-line `user_export_runner.py` — большой, но линейный pipeline;
  стоимость поддержки низкая.

### Риски

- **Rate-limiting у storage backend'ов:** список `done` request'ов
  делает per-row signed URL генерацию. Для 50 items/page = 50
  presigned-URL syscalls. boto3 `generate_presigned_url` локальный
  (HMAC); GCS — тоже локальный (после `Client` init). Нет round-trip'а,
  риск минимальный.
- **InMemoryStorage в production:** если кто-то накосячит с
  `STORAGE_BACKEND` env var, в проде может оказаться InMemory →
  данные потеряны при рестарте контейнера. Mitigation: `build_storage_from_env`
  валидирует backend ∈ {minio, gcs, memory} и `STORAGE_BUCKET` required
  для не-memory; misconfiguration выявится на старте сервиса.
- **Concurrent exports в одной session — race на pending guard:**
  `_ensure_no_active_request` делает SELECT перед INSERT. Между ними
  возможна вставка дубликата (рекорд гонка с самим собой не страшна
  — тот же user, тот же endpoint). Acceptable для now; partial-unique
  constraint может появиться в Phase 4.11b если cluster trace'ы покажут
  проблему.

### Что нужно сделать в коде

- [x] Migration `0021_audit_log_user_actions`.
- [x] `shared_models.storage` (Protocol + 3 backends) + extras.
- [x] `EmailKind.EXPORT_READY` + Jinja2 templates (`en`/`ru`).
- [x] `parser_service.services.user_export_runner`.
- [x] `parser_service.worker.run_user_export_job` + register в WorkerSettings.
- [x] `parser_service.api.users.list_my_requests` — cursor + filters + signed_url.
- [x] `parser_service.api.users.request_export` — enqueue после insert.
- [x] Audit-log entries для EXPORT_REQUESTED / ERASURE_REQUESTED.
- [x] Tests: pagination, filters, isolation, worker happy/failure, ZIP isolation.

## Когда пересмотреть

- Если export size реально начнёт упираться в `export_max_zip_size_mb`
  (default 500) — добавить streaming-ZIP вместо в-memory build'а.
- Если auto-retry окажется нужным после прода (например, transient
  S3 timeouts > X% requests) — добавить tries=3 + idempotency-by-key
  в `enqueue_job`.
- Если Phase 4.11c добавит cascade hard-delete user'а — переоценить
  audit-log retention (часть rows будут указывать на несуществующего
  actor_user_id; FK уже SET NULL, но historical-context теряется).
- Если Phase 8.0 notifications переедет на cursor-pagination —
  promote `_encode_cursor`/`_decode_cursor` в shared helper.

## Ссылки

- Связанные ADR:
  - ADR-0003 (versioning + audit_log baseline)
  - ADR-0033 (Clerk authentication — auth boundary для GDPR endpoints)
  - ADR-0036 (sharing/permissions — определяет «owned» vs «shared»)
  - ADR-0038 (Phase 4.10b stub-now / process-in-4.11 split)
  - ADR-0039 (transactional email — params allowlist уже включает
    `export_url`/`export_size_bytes`/`export_format`)
- GDPR Art. 15 (right of access), Art. 17 (right to erasure),
  Art. 20 (right to data portability), Art. 30 (record of
  processing activities).
- gdpr.eu — practical guidance: 30 days as standard retention for
  data-export downloads.
