# Agent #1 — Phase 10.9a: ORM `AudioSession` + alembic migration + `tree_settings` consent

Ты — инженер на проекте AutoTreeGen / SmarTreeDNA (репозиторий `F:\Projects\TreeGen`).

## Перед началом ОБЯЗАТЕЛЬНО прочитай

1. `CLAUDE.md` — конвенции (комментарии RU, identifiers EN, Conventional Commits,
   Python 3.13, SQLAlchemy 2 async, `uv`, pre-commit must pass, **`--no-verify` запрещён**,
   ветка `feat/<short-name>`, **никогда не коммитить в `main`**).
2. `docs/feature_voice_to_tree.md` — §3.2 «Data model» полностью.
3. `docs/adr/0064-voice-to-tree-pipeline.md` — §«Решение» (B1 per-tree consent, F1 retention).
4. `docs/adr/0049-gdpr-erasure-pipeline.md` — паттерн hard-delete.
5. Existing migrations: `infrastructure/alembic/versions/2026_05_01_0028-*.py` и
   `2026_05_01_0029-*.py` — последние два, шаблон для именования и стиля.
6. Existing ORM: `packages/shared-models/src/shared_models/orm/dna_cluster.py` —
   как устроен новый ORM с миксинами.
7. `packages/shared-models/src/shared_models/orm/__init__.py` — паттерн регистрации.
8. `packages/shared-models/tests/test_schema_invariants.py` — что должно проверяться.

## Задача

Реализовать ORM-слой Phase 10.9a + migration. Никакого I/O, никаких внешних
вызовов — pure ORM definitions + alembic + tests.

## Branch

```text
feat/phase-10-9a-orm-audio-sessions
```

От свежего main: `git checkout main && git pull && git checkout -b feat/phase-10-9a-orm-audio-sessions`.

## Scope

### A. Новая ORM `AudioSession`

Файл: `packages/shared-models/src/shared_models/orm/audio_session.py`.

Поля — **строго** по `feature_voice_to_tree.md` §3.2. Минимально:

```python
class AudioSession(Base, SoftDeleteMixin, ProvenanceMixin):
    __tablename__ = "audio_sessions"

    id: Mapped[UUID]                                # PK, default uuid4
    tree_id: Mapped[UUID]                           # FK trees.id, indexed
    owner_user_id: Mapped[UUID]                    # FK users.id

    # хранилище
    storage_uri: Mapped[str]                        # s3://bucket/audio/{uuid}.webm
    mime_type: Mapped[str]
    duration_sec: Mapped[float | None]
    size_bytes: Mapped[int]

    # транскрипция
    status: Mapped[AudioSessionStatus]             # enum
    language: Mapped[str | None]
    transcript_text: Mapped[str | None]
    transcript_provider: Mapped[str | None]
    transcript_model_version: Mapped[str | None]
    transcript_cost_usd: Mapped[Decimal | None]
    error_message: Mapped[str | None]

    # privacy gate (snapshot consent на момент записи)
    consent_egress_at: Mapped[datetime]            # NOT NULL — критично, см. ниже
    consent_egress_provider: Mapped[str]            # 'openai' | 'self-hosted-whisper'

    created_at, updated_at, deleted_at — стандартные.
```

`AudioSessionStatus` — enum в том же файле:
`uploaded | transcribing | ready | failed`.

**Critical invariant:** `consent_egress_at` **NOT NULL**. Insert без consent
должен падать на DB-уровне. Это последняя линия privacy-gate (см. ADR-0064 §Риски).

### B. Patch `tree_settings`

> **Owner decision 2026-05-01 (Option A):** `tree_settings` таблицы в codebase
> **нет** (проверено). Owner выбрал добавить consent-поля прямо в существующий
> `Tree` ORM. Не создавай новую таблицу — этот выбор пересмотрят в
> Phase 10.9.x если settings разрастутся (ADR-0064 §«Когда пересмотреть»).

Файл: `packages/shared-models/src/shared_models/orm/tree.py` — добавить:

- `audio_consent_egress_at: Mapped[datetime | None]` (default None, nullable)
- `audio_consent_egress_provider: Mapped[str | None]` (default None, nullable;
  string а не enum — позволяет добавить self-hosted-whisper без миграции)

### C. Alembic-миграция

Файл (имя строго по pattern существующих 0028, 0029):
`infrastructure/alembic/versions/2026_05_01_0030-0030_audio_sessions_and_consent.py`.

- `op.create_table('audio_sessions', ...)` со всеми constraints
- `op.add_column('trees', sa.Column('audio_consent_egress_at', sa.DateTime(timezone=True), nullable=True))`
- `op.add_column('trees', sa.Column('audio_consent_egress_provider', sa.String(), nullable=True))`
- Indexes: `audio_sessions(tree_id, deleted_at)`, `audio_sessions(status)`
  (для query «все pending» в worker'е)
- `downgrade()` корректный (drop в обратном порядке)
- `revision`, `down_revision` — связь с предыдущей revision (0029)

### D. Schema invariants

Дополнить `packages/shared-models/tests/test_schema_invariants.py`:

- `audio_sessions.consent_egress_at NOT NULL` — assert через `Inspector`
- ORM `AudioSession` зарегистрирован в `Base.metadata.tables`
- FK on `tree_id` → `trees.id` (ondelete=CASCADE — для erasure совместимости)
- `trees.audio_consent_egress_at` и `trees.audio_consent_egress_provider`
  — оба **nullable** (per Option A, null = consent не дан)

### E. Регистрация в `__init__.py`

`packages/shared-models/src/shared_models/orm/__init__.py` — добавить
`AudioSession`, `AudioSessionStatus` в экспорт. Альфавитный порядок если
он соблюдается.

### F. Тесты

`packages/shared-models/tests/test_audio_session.py`:

- ORM round-trip: create → query → soft-delete → query (excluded by default)
- ProvenanceMixin поля заполняются (`source_files`, `import_job_id`, `manual_edits`)
- Constraint `consent_egress_at NOT NULL` — пытаемся insert без → DB error
- Status enum — invalid value → DB error
- Cascade: delete tree → delete audio_sessions (ondelete CASCADE)

## Definition of Done

- [ ] ORM file + миграция + tests
- [ ] `uv run alembic upgrade head` — passing на свежей dev-БД
- [ ] `uv run alembic downgrade -1` — passing (round-trip)
- [ ] `uv run pytest packages/shared-models -v` — passing
- [ ] `uv run mypy packages/shared-models` — passing strict
- [ ] `uv run ruff check packages/shared-models infrastructure/alembic` — clean
- [ ] `uv run pre-commit run --files <ваши_файлы>` — passing
- [ ] PR-описание ссылается на `ADR-0064` + `feature_voice_to_tree.md` §3.2
- [ ] PR-описание явно говорит: «next agent (#3) теперь импортирует `AudioSession`
      из `shared_models.orm`»

## Что НЕ трогать

- `packages/ai-layer/` — зона #2
- `services/parser-service/` — зона #3
- `apps/web/` — зона #4
- Existing migrations — read-only

## Подводные камни

1. **`tree_settings` может не существовать.** Если его нет — STOP, спроси.
2. **Voice — биометрика, потенциально Art. 9.** На уровне ORM этого не видно,
   но не добавляй случайно `transcribe_features` jsonb и подобное —
   minimization data-model.
3. **`consent_egress_provider`** — string, **не enum** на DB-уровне.
   Это позволяет добавить self-hosted-whisper в Phase 10.9.x без миграции.
   Применять Pydantic `Literal[...]` на app-слое (#3 owns).
4. **Версия revision-id alembic'а** — сверь с last in `alembic_version` table
   на dev-БД (`docker compose up -d postgres`, `alembic current`). Если
   между моментом старта и моментом merge другая ветка зафиксировала 0030 —
   сдвинь свою на 0031.

## Conventional Commits шаблоны

```text
feat(shared-models): add AudioSession ORM with consent gate (Phase 10.9a)
feat(alembic): migration 0030 — audio_sessions table + tree_settings consent fields
test(shared-models): add AudioSession invariants and constraint tests
```
