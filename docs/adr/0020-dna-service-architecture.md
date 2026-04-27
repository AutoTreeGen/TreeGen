# ADR-0020: DNA service — architecture, consent model, encryption design

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `dna`, `service`, `security`, `encryption`, `gdpr`, `phase-6`

## Контекст

Phase 6.0 + 6.1 закрыли DNA-pipeline на уровне библиотеки и CLI:
парсеры (23andMe v5, Ancestry v2), `GeneticMap`,
`find_shared_segments`, `predict_relationship`, и `dna-analysis match`
который читает два raw `.txt` файла и выдаёт JSON. Всё работает на
локальной машине пользователя, ничего не уходит на сервер.

Phase 6.2 — превращение этого в **multi-user service:**

- Пользователь загружает encrypted blob через HTTP API.
- Сервер хранит encrypted blob + метаданные.
- Сервер запускает matching между двумя blob'ами одного пользователя
  (Phase 6.2). Cross-user matching — Phase 6.3+ с обоюдным consent.
- Сервер **никогда** не видит plaintext без пользовательской passphrase.
- Удаление по запросу пользователя — hard delete blob + метаданных
  (GDPR Art. 17), без следа в audit-log за пределами факта события.

Что заставляет принимать решения здесь, а не в Phase 6.1:

1. **Persistence vs in-memory.** ADR-0012 §«Architectural separation»
   зарезервировал `services/dna-service/` под persistence, чтобы
   pure-functions `packages/dna-analysis/` оставался без БД-зависимостей.
   Phase 6.2 — реализация этого контракта.
2. **Multi-user → consent table.** Локальная CLI не нуждалась в
   audit-trail consent; service-режим с возможностью third-party
   matching — нуждается строго (GDPR Art. 6 требует demonstrable
   consent).
3. **Encryption at rest.** ADR-0012 §«Decision» зафиксировал
   application-level Argon2id-key + AES-256-GCM, zero-knowledge.
   Реализация откладывалась до Phase 6.2; здесь — финальный design.
4. **Параллельные агенты.** Phase 6.2 трогает `packages/shared-models/`,
   которые также правит другая команда (entity-resolution). Нужна
   явная стратегия координации.

Силы давления:

- **Privacy by design (CLAUDE.md §3.5, ADR-0012).** Никакого читаемого
  DNA на сервере при компрометации БД.
- **GDPR-compliance.** Consent → demonstrable + revocable (Art. 7);
  data minimisation (Art. 5); right to erasure (Art. 17, Art. 33
  72-hour breach notification).
- **Production realism.** Если сервис мёрзлый или сильно
  сложный, пользователи будут продолжать гонять CLI на локальном
  диске и обходить consent-tracking. Дизайн должен быть **простой
  enough** для ежедневного использования.
- **Testability в CI.** Тесты не должны требовать настоящего
  Argon2id-derivation на каждом запуске (60s+ каждый прогон).
  Encryption должен быть pluggable / mockable.

## Рассмотренные варианты

### Encryption: Вариант A — full zero-knowledge с Argon2id (выбран как target, но в два этапа)

Browser (Phase 4.x web app) хранит passphrase в session memory.
Argon2id KDF → 256-bit key → AES-256-GCM шифрует raw DNA blob → сервер
получает уже ciphertext.

- ✅ Compromise сервера = только ciphertext.
- ✅ Соответствует ADR-0012 §«Decision».
- ✅ Marketing-аргумент («мы не можем читать вашу DNA даже если захотим»).
- ❌ Browser-side Argon2id занимает 1-3 секунды UX-pause + WebCrypto
  doesn't expose Argon2 нативно (нужен argon2-browser WASM).
- ❌ Recovery flow сложный: passphrase loss = data loss; recovery code
  на момент upload (one-time, юзер хранит сам).
- ❌ Background jobs (matching между двумя blob'ами одного пользователя
  без active session) требуют либо TTL session-key, либо
  ephemeral re-prompt — это Phase 6.4 ADR.

### Encryption: Вариант B — server-side AES с rotated KEK

Сервер владеет ключом (rotated через KMS), DNA лежит зашифрованной
тем же KEK.

- ✅ Простая реализация, любой ORM-tool с column-encryption справится.
- ❌ Compromise сервера = plaintext. Не zero-knowledge → нарушает
  ADR-0012 §«Decision».
- ❌ Не differentiator vs Ancestry/MyHeritage.
- Нерассматриваемо как final solution.

### Encryption: Вариант C — Phase 6.2 без encryption, добавить позже

Phase 6.2 ставит skeleton + consent + storage path **plain-text**;
encryption пакетом отдельным — Phase 6.2.x (после soak-window).

- ✅ Резко снижает blast radius Phase 6.2 (фокус на consent + flow).
- ✅ Encryption — отдельный crypto-review, не смешивается с API/ORM
  работой.
- ❌ Любая задержка с Phase 6.2.x = **plaintext DNA на диске**.
  Mitigation: явный feature-flag `DNA_REQUIRE_ENCRYPTION=true` уже в
  Phase 6.2, при `false` сервис принимает только test-fixtures (см.
  «Решение» ниже).

### Consent table: Вариант D — single boolean per user

`users.dna_consent_signed_at` поля без отдельной таблицы.

- ✅ Самый простой.
- ❌ Не tracks per-kit consent (что если у юзера два kit'а — свой и
  родственника?). Нужны **per-kit** consent с разным kit_owner.
- ❌ Не tracks `consent_text` snapshot — юристы не смогут доказать,
  на что конкретно согласился пользователь, если terms менялись.
- ❌ Не tracks revocation как event (только бы deleted_at).

### Consent table: Вариант E — full audit-trail consent (выбран)

Отдельная таблица `dna_consents` с `kit_owner_email`, `consent_text`
snapshot, `consented_at`, `revoked_at`, immutable rows (новая запись
при изменении вместо update).

- ✅ Полная audit-trail для GDPR-вопросов.
- ✅ Per-kit consent для kit'ов родственников (`kit_owner_email != user_email`).
- ✅ `consent_text` snapshot привязывает kit к конкретной версии terms.
- ❌ Чуть больше строк в БД на upload (но measurable cost — ~200 байт
  на consent record).

### Storage: Вариант F — local filesystem per upload (выбран для MVP)

Каждый upload пишется как отдельный файл `<storage_root>/<uuid>.bin`.
В БД лежит только metadata (sha256, snp_count, provider, storage_path).

- ✅ Простая реализация, работает локально и в Cloud Run с persistent
  disk.
- ✅ Лёгко мигрировать в S3/MinIO (Phase 6.x): тот же интерфейс
  `Storage` с двумя реализациями.
- ❌ Не годится для горизонтального scale-out без shared filesystem.
  Mitigation: для MVP single-pod достаточно; production — S3 в Phase 6.x.

### Storage: Вариант G — column в БД (`bytea`)

Encrypted blob прямо в строке `dna_test_records`.

- ✅ Atomic с metadata; backup БД = backup данных.
- ❌ Postgres не оптимизирован под 10-50 МБ blob'ы; TOAST overhead.
- ❌ Migration-стратегия в S3 потом сложнее.

## Решение

Принят **гибрид: Вариант A (encryption target) + C (этапированная
поставка) + E (full consent table) + F (filesystem storage MVP).**

### Phase 6.2 scope (этот PR-комплект)

Service skeleton, который запускается end-to-end на synthetic data
**без real encryption**, но с архитектурой, готовой к нему:

- **`services/dna-service/`** FastAPI:
  - `POST /consents` — создать consent record (`kit_owner_email`,
    `consent_text` snapshot, `consented_at` = server time).
  - `GET /consents/{id}` — прочитать.
  - `DELETE /consents/{id}` — revoke + cascade hard-delete blob и
    metadata.
  - `POST /dna-uploads` — multipart, требует `consent_id`. Принимает
    blob (в Phase 6.2 — plaintext с feature-flag, см. ниже), пишет
    через `Storage.write()` интерфейс, возвращает `DnaTestRecord`.
  - `POST /matches` — `{test_a_id, test_b_id}`, оба должны принадлежать
    одному пользователю и иметь активный consent. Загружает blob'ы
    в memory, парсит, запускает Phase 6.1 `find_shared_segments` +
    `predict_relationship`, возвращает derived stats. **Никакого raw
    в response.**
  - `GET /healthz` — liveness probe.
- **`packages/shared-models/orm/`:** добавить `DnaConsent` и
  `DnaTestRecord` (см. Task 2 в брифе).
- **Migration:** `infrastructure/alembic/versions/...` — две новые
  таблицы.
- **Feature flag `DNA_REQUIRE_ENCRYPTION`:** default `true`. При `true`
  `POST /dna-uploads` отвергает любой blob, у которого первый байт
  не соответствует encryption-magic-header (Phase 6.2.x определит
  формат). При `false` — принимает plaintext, явно отмечает
  `DnaTestRecord.encryption = "none"` и отвечает HTTP 200 с
  `X-Warning: dna-encryption-disabled`. Тесты гоняют с `false` против
  synthetic 23andMe / Ancestry fixtures из Phase 6.0.
- **`Storage` интерфейс:** `LocalFilesystemStorage` единственная
  реализация в Phase 6.2; `S3Storage` — Phase 6.x с тем же интерфейсом.

### Phase 6.2.x scope (отдельный PR-комплект, отдельный crypto-review)

- Browser-side Argon2id KDF (через WASM) + AES-256-GCM encryption в
  `apps/web/src/lib/dna-crypto.ts`.
- Server-side `EncryptedBlobValidator` проверяет magic header + не
  принимает plaintext.
- Recovery-code UI flow.
- Tests на encryption round-trip с синтетическим passphrase.

### Phase 6.3 scope (cross-user matching, future ADR-0021)

- `dna_consents.scope` enum: `OWNER_ONLY` (default) vs `OPEN_TO_MATCHES`.
- Cross-user matching доступно только если **оба** kit'а имеют
  `OPEN_TO_MATCHES` + явное «I consent to my DNA being compared with
  other AutoTreeGen users» с отдельной checkbox.
- Notification flow при найденном match'е.

### Consent revocation flow (Phase 6.2)

При `DELETE /consents/{id}`:

1. Найти все `DnaTestRecord` с этим `consent_id`.
2. Для каждого: `Storage.delete(storage_path)` (overwrite + unlink).
3. Hard delete `DnaTestRecord` rows.
4. Set `dna_consents.revoked_at = now()` (consent record остаётся для
   audit, но не привязан к данным).
5. Audit-log: `dna_consent_revoked` event (только factum, без kit_id /
   sha256 / storage_path — чтобы revoke действительно стирал
   associativity).
6. HTTP 204 No Content.

Если `Storage.delete()` падает (например, файл уже отсутствует) —
**не блокируем revocation:** консент должен быть отозван даже если
файлы не нашлись.

### Coordination с другими агентами (shared-models)

Брифинг отмечает риск конфликтов. Стратегия:

1. Перед `git commit` любого изменения в `packages/shared-models/`:
   `git fetch origin main && git rebase origin/main`. Если конфликт —
   resolve, прогнать `uv run pytest packages/shared-models/`, заново
   коммитить.
2. Имена тегов миграций — глобально уникальные UUID-prefix'ы (alembic
   генерирует автоматически), `down_revision` берётся от текущего
   `head` после rebase.
3. ORM модели — отдельные файлы (`dna_consent.py`, `dna_test_record.py`),
   только `__init__.py` — общая точка слияния. Перед коммитом
   sanity-check: `git diff origin/main -- packages/shared-models/src/shared_models/orm/__init__.py`.

## Последствия

**Положительные:**

- DNA-флоу становится production-ready: upload + consent + match через
  HTTP API.
- Architecture готова к real encryption (Phase 6.2.x): feature-flag,
  `Storage` interface, JSON output без raw genotypes — всё на месте.
- Consent table с audit-trail закрывает class GDPR-вопросов
  (demonstrable consent, retention transparency, revocation).
- Cross-user matching не запускается случайно — Phase 6.3 поднимает
  отдельный consent-scope, default закрыт.

**Отрицательные / стоимость:**

- ~600 LOC Python + 1 alembic migration + ~200 LOC tests в Phase 6.2.
- `DNA_REQUIRE_ENCRYPTION=false` для CI и dev — рисковая дверь, если
  кто-то забудет переключить в prod. Mitigation: при `false` сервис
  логирует warning **на каждом upload**, и `/healthz` возвращает
  `dna_encryption_required: false` в payload — easy для prod alerting.
- В Phase 6.2 plaintext DNA технически на диске. **Никто не должен
  заливать туда реальные DNA до Phase 6.2.x.** Документируется
  большим warning в `dna-service-deployment.md` runbook.

**Риски:**

- **Phase 6.2.x задерживается** → plaintext DNA остаётся в продлении.
  *Mitigation:* `DNA_REQUIRE_ENCRYPTION` default `true`, dev-mode
  `false` явно требуется в env. Owner может ставить feature-flag
  на «prod readiness» проверку deploy-tooling.
- **Storage.delete() race** → blob удалён, БД-row остаётся.
  *Mitigation:* idempotent delete (повторный вызов — no-op), периодическая
  reconciliation job (Phase 6.x).
- **Pass-through bug:** случайно вернули raw blob в HTTP response.
  *Mitigation:* type-checked Pydantic response models БЕЗ blob поля;
  тесты на отсутствие raw в response (`assert b"\\t" not in response.content`
  — нет TSV-структуры в JSON).
- **Параллельные edits в shared-models** → конфликтные миграции.
  *Mitigation:* explicit rebase-and-resolve flow выше; миграционные
  имена через alembic UUID, не коллидируют.

**Что нужно сделать в коде (Phase 6.2):**

1. ADR-0020 (этот PR).
2. `packages/shared-models/src/shared_models/orm/dna_consent.py` —
   `DnaConsent` ORM. `dna_test_record.py` — `DnaTestRecord` ORM.
   Update `__init__.py`. Coordinated rebase before commit.
3. Alembic migration в `infrastructure/alembic/versions/`:
   `add_dna_consents_and_test_records`.
4. `services/dna-service/`: pyproject.toml, `main.py`, `config.py`,
   `database.py`, `api/{healthz,consents,uploads,matches}.py`,
   `services/{storage,matcher}.py`, tests.
5. Workspace plumbing: добавить `services/dna-service` в корневой
   `pyproject.toml` `[tool.uv.workspace.members]`.

## Когда пересмотреть

- **Phase 6.2.x ship'ится** → удалить `DNA_REQUIRE_ENCRYPTION=false`
  как default; документировать в release notes.
- **Cross-user matching готовится** → ADR-0021 для consent scope +
  notification flow + matching API extensions.
- **Storage.local выходит из scope** (multi-pod prod) → ADR для S3
  migration с rolling re-upload.
- **GDPR DPIA review** → возможные доп. требования (consent text
  versioning, sub-consent для each operation).
- **Browser-side Argon2id оказывается слишком медленным** (>5s на
  laptop) → пересмотр KDF parameters или server-assist через Oblivious
  Pseudo-Random Function (OPRF).
- **Performance pairwise matching > 2 минут** на real-size DNA →
  background-task через `arq` (Redis), HTTP возвращает job-id +
  poll endpoint.

## Ссылки

- Связанные ADR:
  - ADR-0012 (DNA processing privacy & architecture) — фундамент,
    задаёт zero-knowledge принцип и архитектурное разделение.
  - ADR-0014 (DNA matching algorithm) — Phase 6.1 алгоритм,
    переиспользуется как `services.matcher`.
  - ADR-0003 (versioning strategy) — DNA opts out of soft-delete
    (см. ADR-0012); revocation = hard delete.
  - ADR-0009 (genealogy integration strategy) — DNA gap, GEDmatch
    (Phase 6.x).
  - Будущий ADR-0021 (Phase 6.3) — cross-user matching consent scope.
- CLAUDE.md §3.5 (Privacy by design), §5 (запреты — DNA в репо).
- ROADMAP §10 (Phase 6 — DNA Analysis Service), §17 (Phase 13 —
  безопасность и деплой).
- Внешние:
  - [GDPR Art. 6](https://gdpr-info.eu/art-6-gdpr/) — lawfulness of processing.
  - [GDPR Art. 7](https://gdpr-info.eu/art-7-gdpr/) — conditions for consent.
  - [GDPR Art. 17](https://gdpr-info.eu/art-17-gdpr/) — right to erasure.
  - [GDPR Art. 33](https://gdpr-info.eu/art-33-gdpr/) — breach notification.
  - [Argon2 RFC 9106](https://datatracker.ietf.org/doc/html/rfc9106).
  - [WebCrypto API spec](https://www.w3.org/TR/WebCryptoAPI/) — для Phase 6.2.x.
  - [argon2-browser WASM](https://github.com/antelle/argon2-browser).
