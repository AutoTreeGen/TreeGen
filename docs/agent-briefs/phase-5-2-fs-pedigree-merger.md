# Agent brief — Phase 5.2: FamilySearch pedigree merger

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-fs-merger`.
> Phase 5.0 (FS client) и 5.1 (FS importer) — в main. Сейчас FS persons
> приземляются как **новые** записи в дереве. Фаза 5.2 — детектировать
> и предлагать merge с существующими persons.
> Перед стартом: `CLAUDE.md` §5 (запрет авто-merge),
> `docs/agent-briefs/phase-5-1-familysearch-import.md`,
> `docs/agent-briefs/phase-4-6-merge-persons-ui.md` (твоя коллега
> Agent 1 шипила manual merge UI; ты переиспользуешь её infrastructure).

---

## Зачем

Phase 5.1 импортит FS pedigree — но если у юзера уже есть Иван Иванов
в дереве из локального GED, то FS-импорт создаёт второго Ивана. Phase 5.2
делает: на FS-import триггерится duplicate detection → выводится в
существующий dedup review queue (Phase 4.5 UI) → юзер ревьюит и мерджит
через Phase 4.6 UI.

Никакого нового merge UI не создаём. Просто **подключаем FS-import
к существующему dedup pipeline**.

---

## Что НЕ делать

- ❌ Auto-merge даже high-confidence FS↔local пар. Через review queue.
- ❌ Скачивать всю мировую FS базу. Только текущий import scope.
- ❌ Перезаписывать local data FS-данными. FS — отдельный source с
  provenance, original survives.
- ❌ Дублировать merge logic из Phase 4.6 — **используй её**.
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — duplicate detection в FS importer

**Файл:** `services/parser-service/src/parser_service/services/familysearch_importer.py`
(существующий из 5.1).

После того как FS person приземлён в БД с `provenance.source: "familysearch"`,
вызови существующий dedup scorer (Phase 3.4 entity-resolution) против
**всех existing persons в этом tree БЕЗ FS-source**.

Если найдены candidates с `score >= 0.6`:

- Создать запись в `duplicate_suggestions` (table из Phase 4.5):
  `subject_id=fs_person.id`, `candidate_id=local_person.id`,
  `score`, `reason: "fs_import_match"`, `status: "pending_review"`.

⚠ Не блокируй FS-import если scorer тормозит — асинхронно через
`arq` enqueue если возможно, иначе sync но с timeout.

### Task 2 — UI hint в FS import endpoint response

**Файл:** `services/parser-service/src/parser_service/api/imports.py`.

Existing `POST /imports/familysearch` endpoint расширить:

```json
{
  "imported_persons": 42,
  "imported_events": 38,
  "duplicate_suggestions_created": 7,
  "review_url": "/trees/{tree_id}/duplicates"
}
```

Frontend (Phase 5.x UI если есть, либо просто вывод в JSON) использует
эту ссылку чтобы юзер сразу пошёл ревьюить.

### Task 3 — Cross-source dedup nuances

Документируй в `docs/fs-dedup-rules.md`:

1. FS persons обычно имеют `fs_pid` (FamilySearch person ID). Сохранять
   в `provenance.fs_pid` для idempotency повторных импортов.
2. Если local person уже linked to fs_pid X (через предыдущий merge),
   повторный import того же fs_pid НЕ создаёт duplicate suggestion
   (idempotent skip).
3. Если FS person оказался merge-кандидатом, но user отверг merge
   (`status: rejected`), повторный import не предлагает снова в течение
   90 дней (cooldown).

### Task 4 — Тесты

**Файл:** `services/parser-service/tests/test_fs_dedup.py`.

- Synthetic FS pedigree содержит «Иван Иванов 1850». Local tree уже
  имеет такого. Import → 1 duplicate_suggestion создан.
- FS person с уникальным именем → 0 suggestions.
- Idempotency: тот же FS-import дважды → suggestions не дублируются.
- Cooldown: rejected suggestion + reimport в течение 90 дней → skip.

### Task 5 — Финал

1. ROADMAP §5.2 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-5.2-fs-pedigree-merger`.
4. CI green до merge. Никакого `--no-verify`.

---

## Сигналы успеха

1. ✅ FS-import триггерит dedup scorer против local.
2. ✅ Suggestions попадают в существующий review queue (Phase 4.5 UI).
3. ✅ Idempotency работает на повторных импортах.
4. ✅ Cooldown 90 дней работает.
5. ✅ E2E manual test: import синтетический FS → suggestion появляется
    в `/trees/{id}/duplicates` page.

---

## Если застрял

- arq не настроен в проекте → sync с timeout, follow-up для async.
- entity-resolution scorer signature непонятна → читай Phase 3.4
  тесты, там примеры.
- FS-mock непонятен → используй Phase 5.0 mock fixtures.

Удачи.
