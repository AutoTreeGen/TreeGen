# Agent brief — Phase 5.2.1: FsDedupAttempt implementation

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-fs-merger`
> (preserved с твоей же Phase 5.2 discovery — branch
> `docs/phase-5-2-dedup-discovery` already merged via PR #88, можешь
> reuse worktree или создать fresh).
> Перед стартом: PR #88 в main (твой research doc),
> `docs/research/phase-5-2-dedup-discovery.md`,
> `packages/shared-models/src/.../orm.py` (особенно `PersonMergeLog`
> как pattern-reference), `services/parser-service/services/familysearch_importer.py`
> (Phase 5.1 в main), `services/parser-service/services/dedup_finder.py`,
> `services/parser-service/schemas` (где живёт Pydantic
> `DuplicateSuggestion` — НЕ трогать имя).

---

## Зачем

PR #88 принял **option C** — узкая table `fs_dedup_attempts` для
FS-imported persons. Сейчас имплементируем.

Архитектурные точки уже зафиксированы в твоём research doc:

- timestamp-state model (как `PersonMergeLog`)
- directional pair (`fs_person_id` всегда первым, без lex-reorder)
- partial unique index по active-state
- 90-day cooldown на rejected
- idempotency на повторный import того же `fs_pid`

---

## Что НЕ делать

- ❌ Не трогать имя `DuplicateSuggestion` (Pydantic class в schemas).
  Используем `FsDedupAttempt` (ORM) + `FsDedupAttemptOut` (Pydantic).
- ❌ Не auto-merge кандидатов — только enqueue в review UI.
- ❌ Не lex-reorder pair (fs_person_id || candidate_id). Direction matters.
- ❌ Не блокировать FS import если scorer медленный — async / sync с timeout.
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — ORM + Alembic

**Файл:** `packages/shared-models/src/.../orm.py`

```python
class FsDedupAttempt(Base):
    __tablename__ = "fs_dedup_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tree_id: Mapped[int] = mapped_column(ForeignKey("trees.id"), index=True)
    fs_person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    candidate_person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    score: Mapped[float]  # 0..1 от entity-resolution scorer
    reason: Mapped[str | None]  # 'fs_import_match' и т.п.
    fs_pid: Mapped[str | None] = mapped_column(index=True)  # FS person ID для idempotency

    # Timestamp-state (mirrors PersonMergeLog pattern):
    rejected_at: Mapped[datetime | None]
    merged_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    provenance: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Active-state partial unique index (set in Alembic):
    # WHERE rejected_at IS NULL AND merged_at IS NULL
    # Prevents duplicate active attempts for same directional pair.
```

Alembic migration: новая ревизия (следующий номер после текущего).
Включает:

- `CREATE TABLE fs_dedup_attempts (...)`
- `CREATE INDEX ix_fs_dedup_attempts_tree_id_fs_pid ON fs_dedup_attempts (tree_id, fs_pid);`
- **Partial unique index** для active-state:

```sql
CREATE UNIQUE INDEX ux_fs_dedup_attempts_active_pair
ON fs_dedup_attempts (tree_id, fs_person_id, candidate_person_id)
WHERE rejected_at IS NULL AND merged_at IS NULL;
```

⚠ Watch: Agent 6 (Phase 4.9) тоже трогает `orm.py` — добавляет review
fields на Hypothesis. `git pull --rebase` обязательно перед commit.

### Task 2 — FS importer hook

**Файл:** `services/parser-service/src/parser_service/services/familysearch_importer.py`

После того как FS person upserted в БД, вызови dedup scorer
(`dedup_finder.find_person_duplicates` или эквивалент из
entity-resolution package) против non-FS persons в дереве.

Для каждого кандидата с `score >= 0.6`:

1. Idempotency check: `fs_pid` уже имеет `merged_at` → skip (already
   resolved).
2. Cooldown check: `(fs_person_id, candidate_id)` имеет `rejected_at`
   за последние 90 дней → skip.
3. Active-pair check: уже есть active attempt → skip (partial unique
   index это enforces, но проверяем заранее чтобы не ловить exception).
4. Insert `FsDedupAttempt` с `score`, `reason='fs_import_match'`,
   `fs_pid`.

Добавь в response endpoint POST /imports/familysearch:

```json
{
  "imported_persons": 42,
  "imported_events": 38,
  "fs_dedup_attempts_created": 7,
  "review_url": "/trees/{tree_id}/dedup-attempts"
}
```

### Task 3 — Review API endpoints

**Файл:** `services/parser-service/src/parser_service/api/dedup_attempts.py` (новый)

```text
GET  /trees/{tree_id}/dedup-attempts?status=pending&limit=50
POST /dedup-attempts/{id}/reject  body {reason?: str}
POST /dedup-attempts/{id}/merge   body {confirm: true}
                                   → marks merged_at, redirects к Phase 4.6 merge UI
```

`status` filter:

- `pending` (default): `rejected_at IS NULL AND merged_at IS NULL`
- `rejected`: `rejected_at IS NOT NULL`
- `merged`: `merged_at IS NOT NULL`
- `all`: без фильтра

### Task 4 — Тесты

**Файл:** `services/parser-service/tests/test_fs_dedup_attempts.py`

- Happy path: synthetic FS pedigree содержит «Иван Иванов 1850», local
  tree уже имеет такого. Import → 1 active attempt.
- Idempotency: тот же `fs_pid` повторно → второй attempt не создан
  если первый ещё active.
- Idempotency после merge: `fs_pid` с `merged_at` → reimport skip,
  no new attempt.
- Cooldown: rejected attempt + reimport на 31 день → skip. На 91 день
  → новый attempt создан.
- Direction stability: insert pair (A=fs, B=local), потом attempt с
  (B=fs, A=local) — это **разные** строки, не должны коллизировать
  через partial unique.
- Active-pair unique: две одновременные attempts на same active pair
  → вторая падает на partial unique constraint, importer логирует
  warning + skip.

### Task 5 — Финал

1. ROADMAP §5.2.1 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-5.2.1-fs-dedup-attempts`.
4. CI green до merge. Никакого `--no-verify`.

---

## Сигналы успеха

1. ✅ ORM + миграция в main (partial unique index работает).
2. ✅ FS-import триггерит attempts на synthetic GED.
3. ✅ Idempotency + cooldown работают (manual test + unit tests).
4. ✅ API endpoints отвечают correctly на pending/rejected/merged filters.
5. ✅ Direction-aware (не lex-reorder) — тест passes.

---

## Если застрял

- Partial unique syntax → SQLAlchemy `Index('...', condition=...)` в
  `__table_args__`, или raw SQL в Alembic op.execute().
- entity-resolution scorer signature → читай Phase 3.4 тесты, там
  примеры. Если не понятно — дамп current usage в dedup_finder.py
  обнаружит.
- Phase 4.9 (Agent 6) или Phase 7.5 (Agent 2) merged между rebase'ами
  → обычный rebase + resolve, классы независимы.
- Нет clear FS mock fixtures → Phase 5.0 / 5.1 уже есть, поищи в
  packages/familysearch-client/tests/fixtures.

Удачи. Это закрывает Phase 5.2 полностью.
