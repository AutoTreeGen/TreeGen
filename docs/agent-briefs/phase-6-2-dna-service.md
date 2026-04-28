# Agent brief — Phase 6.2: DNA service (consent + encrypted storage)

> **Кому:** Агент 4 — после Phase 6.1 (DNA matching CLI).
> **Worktree:** свой или новый.
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

Phase 6.1 (твоя работа) — DNA matching CLI работает локально на raw .txt.
Но для AutoTreeGen-as-a-product нужен **service** который:

- Принимает upload encrypted DNA
- Хранит consent
- Запускает matching по запросу
- Не светит raw genotypes никуда кроме memory

Phase 6.2 — **DNA service skeleton + encryption + consent**.

CLAUDE.md §5 + ADR-0012 (твой) — основа privacy.

**Параллельно работают** другие агенты.

**Твоя территория:**

- `services/dna-service/` — **новый сервис** (или существующий, проверь)
- `infrastructure/alembic/versions/` — новая миграция для consent table
- `packages/shared-models/orm.py` — добавить DnaConsent + DnaTestRecord ORM
  (КООРДИНИРУЙ — другие агенты могут править shared-models)
- ADR-0020 — DNA service architecture + key management

**Что НЕ трогай:**

- `services/parser-service/`, `apps/web/`, `packages/familysearch-client/`,
  `packages/entity-resolution/`, `packages/inference-engine/`,
  `packages/gedcom-parser/`

---

## Задачи

### Task 1 — docs(adr): ADR-0020 DNA service architecture

- Status, tags: dna, security, encryption, phase-6
- Контекст: переход от CLI к service
- Encryption design:
  - **Per-user envelope:** server stores encrypted blob; user holds AES-256
    key derived from passphrase (Argon2id)
  - User logs in → passphrase → derives key in browser → key never sent
    to server (zero-knowledge)
  - Server cannot read DNA without user's passphrase
- Consent table:

  ```sql
  CREATE TABLE dna_consents (
      id UUID PRIMARY KEY,
      user_id UUID NOT NULL,
      kit_owner_email TEXT NOT NULL,  -- кто согласился
      consented_at TIMESTAMP NOT NULL,
      consent_text TEXT NOT NULL,  -- snapshot версии consent terms
      revoked_at TIMESTAMP NULL,
      ...
  );
  ```

- Если consent revoked — encrypted blob deleted immediately, audit log
- Decision: deferred actual encryption to Phase 6.2.x — start with
  service skeleton + consent table + storage path stub

### Task 2 — feat(shared-models): DnaTestRecord + DnaConsent ORM

В `packages/shared-models/src/shared_models/orm.py`:

```python
class DnaConsent(Base, TreeOwnedMixins):
    __tablename__ = "dna_consents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[UUID]
    kit_owner_email: Mapped[str]
    consent_text: Mapped[str]
    consented_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

class DnaTestRecord(Base, TreeOwnedMixins):
    __tablename__ = "dna_test_records"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_uuid)
    consent_id: Mapped[UUID] = mapped_column(ForeignKey("dna_consents.id"))
    storage_path: Mapped[str]   # filesystem path к encrypted blob
    sha256: Mapped[str]         # of encrypted content
    snp_count: Mapped[int]
    provider: Mapped[str]       # "23andme", "ancestry", etc
    uploaded_at: Mapped[datetime]
```

**КООРДИНИРУЙ:** перед коммитом сделай `git pull --rebase` и проверь нет ли
параллельных правок в orm.py. Если есть — резолвь, тестируй.

Alembic migration:

```text
uv run alembic revision -m "add_dna_consents_and_test_records"
```

Tests в shared-models.

### Task 3 — feat(dna-service): scaffold FastAPI service

```text
services/dna-service/
  pyproject.toml
  src/dna_service/
    __init__.py
    main.py          # FastAPI app
    config.py
    database.py
    api/
      consents.py    # POST /consents, DELETE /consents/{id}
      uploads.py     # POST /dna-uploads (encrypted blob)
      matches.py     # POST /matches (compute pair, return derived stats)
    services/
      storage.py     # write/read encrypted blobs to filesystem
      matcher.py     # uses dna-analysis package
  tests/
    test_healthz.py
    test_consents.py
    test_uploads.py
```

Dependencies: fastapi, sqlalchemy, asyncpg, dna-analysis (workspace).

`POST /matches` принимает {test_a_id, test_b_id} → загружает оба blob'а,
**в memory** decrypts (passphrase from request header), runs Phase 6.1
matching, returns derived stats (никаких raw в response).

Запиши в workspace pyproject.toml.

### Task 4 — tests с synthetic DNA + consent flow

- test_consent_create_and_revoke
- test_upload_requires_consent
- test_match_requires_both_consents_active
- test_revoke_deletes_blob

### Task 5 (опционально) — docs(runbook): DNA service deployment

`docs/runbooks/dna-service-deployment.md`:

- Где хранить encrypted blobs (local filesystem default, MinIO/S3 для prod)
- Backup strategy (encrypted backups)
- GDPR right-to-deletion procedure

---

## Что НЕ делать

- ❌ Хранить passphrase или derived key на сервере
- ❌ Логировать raw DNA / genotypes
- ❌ Auto-process DNA без consent
- ❌ Cross-user DNA matching без обоюдного consent
- ❌ Real DNA в коммитах (как и в 6.1)
- ❌ Web UI (Phase 4.x)
- ❌ `git commit --no-verify`

---

## Сигналы успеха

1. ✅ ADR-0020
2. ✅ DnaConsent + DnaTestRecord в shared-models с migration
3. ✅ services/dna-service scaffold работает (`/healthz` 200)
4. ✅ Consent CRUD endpoints работают
5. ✅ Mock upload + match flow tested
6. ✅ Все CI green

Также: можешь сделать quick fix-PR для пустого `dna-data-handling.md`
runbook (Phase 6.0 task 5 — был empty content). Это 5-минут отдельный PR.

Удачи. Это где DNA становится production-ready.
