# Agent brief — Phase 4.6: Manual-review person merge

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Используй worktree
> `../TreeGen-merge`. Не трогай `apps/web/.../search/*` (Agent 6, Phase 4.4)
> и `apps/web/.../review/*` (твоя же предыдущая Phase 4.5 — отдельно).
> Перед стартом: `CLAUDE.md` §5 (запрет авто-merge), `ROADMAP.md`,
> `docs/agent-briefs/phase-4-5-dedup-suggestions-ui.md`,
> существующий код `apps/web/src/app/trees/[id]/duplicates/`.

---

## Зачем

Phase 4.5 отдала UI с suggestions, но кнопки «Merge» отключены и
показывают «Coming in 4.6». Сейчас 4.6 — включаем. Жёстко
**manual-review only**: side-by-side, dry-run, явное подтверждение.
Никакого one-click merge, никаких heuristic-overrides.

CLAUDE.md §5: "Автоматический merge персон с близким родством без
manual review" — forbidden. Это правило теперь будет в коде через
обязательный `confirm: true` поле.

---

## Что НЕ делать

- ❌ Auto-merge даже для high-confidence пар. Всегда manual review.
- ❌ One-click merge без preview. Сначала dry-run, потом commit.
- ❌ Permanent destructive merge. Должен быть undo (через soft-delete +
  audit log).
- ❌ Merge персон если одна из них уже участник active hypothesis с
  conflicting evidence — блокировать с понятным message.
- ❌ `--no-verify`. Прямые коммиты в main.

---

## Задачи

### Task 1 — ADR-0022: merge data flow

**Файл:** `docs/adr/0022-person-merge-strategy.md`

Реши и зафиксируй:

1. **Который Person остаётся (`survivor`), который уходит (`merged_into`)**?
   Предложение: пользователь выбирает в UI; default — тот что с большим
   `provenance.source_files` count (более «обоснованный»).
2. **Что делает с полями**? Survivor хранит canonical, merged → soft-delete
   с `merged_into=survivor.id`. Поля merged'а доступны через UI «View
   merge history».
3. **События / отношения / participants**: переключаются на survivor;
   дубликаты event'ов схлопываются по `(date, place, type)` +
   provenance объединяется.
4. **Гипотезы** (если уже есть Hypothesis ORM из Phase 7.2):
   `HypothesisEvidence` с `person_id=merged.id` → `survivor.id`. Если
   это создаёт конфликт (одна гипотеза «X — отец Y», другая «X — не
   отец Y», и Y оказывается merged) — блокировать merge с message
   «Resolve conflicting hypotheses first».
5. **Audit trail**: запись в `person_merge_log` с `survivor_id`,
   `merged_id`, `merged_at`, `merged_by` (user_id когда auth появится,
   пока NULL OK), `dry_run_diff_json` (полный snapshot изменений).
6. **Undo**: 90-day retention, отдельный endpoint
   `POST /persons/merge/{merge_id}/undo`. После 90 дней — hard delete
   merged person, undo невозможен.

### Task 2 — Backend: dry-run + commit endpoints

**Файлы:** `services/parser-service/src/parser_service/api/persons.py`, плюс новый `services/parser-service/src/parser_service/services/person_merger.py`.

Endpoints:

1. `POST /persons/{id}/merge/preview` body `{target_id: int}` →
   возвращает diff: что изменится, какие events схлопнутся, какие
   hypotheses затронуты. **Не пишет в БД**.
2. `POST /persons/{id}/merge` body `{target_id: int, confirm: true,
   merge_id?: str}` → выполняет merge атомарно (одна транзакция).
   `confirm: true` обязателен; без него 400.
3. `POST /persons/merge/{merge_id}/undo` → откат, если в окне 90 дней.
4. `GET /persons/{id}/merge-history` → список merge'ей где person участник.

Тесты в `tests/test_person_merger.py`:

- Happy path: merge two duplicate persons.
- Conflict: merge заблокирован hypothesis conflict.
- Idempotency: один и тот же merge_id дважды — без побочных эффектов.
- Undo в окне работает; undo через 91 день — 410 Gone.
- Без `confirm:true` — 400.

### Task 3 — Frontend: merge review page

**Файл:** `apps/web/src/app/trees/[id]/persons/[id]/merge/page.tsx`
(или `apps/web/src/app/persons/[id]/merge/[targetId]/page.tsx` —
выбери что в существующей роутинг-схеме).

UI:

1. **Side-by-side**: две колонки (survivor | merged). Каждое поле
   подсвечивается: green=match, yellow=different, red=conflict.
2. **Diff preview**: что survivor получит после merge (calls preview
   endpoint).
3. **Choose survivor** toggle: «Keep left» / «Keep right» — меняет
   которая колонка остаётся.
4. **Confirm dialog**: «Merge X into Y? This will soft-delete Y. You
   have 90 days to undo.» + checkbox «I reviewed the diff above» +
   кнопка «Confirm merge» (disabled пока checkbox не отмечен).
5. **Success state**: «Merged. Undo within 90 days.» + кнопка undo.
6. **Error state**: «Merge blocked: `<reason>`» (e.g. hypothesis conflict).

Интеграция с Phase 4.5: на review page кнопка «Merge» больше не
disabled — ведёт на этот merge URL. Удалить «Coming in 4.6» подпись.

### Task 4 — Финал

1. ROADMAP §4.6 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-4.6-merge-persons-ui` с описанием + скриншот UI +
   ссылка на ADR-0022.
4. CI green до merge. Никакого `--no-verify`.

---

## Сигналы успеха

1. ✅ ADR-0022 в `docs/adr/`.
2. ✅ Preview endpoint работает без записи в БД.
3. ✅ Commit endpoint требует `confirm:true`, без — 400.
4. ✅ Undo в окне 90 дней работает.
5. ✅ Phase 4.5 review page больше не показывает «Coming in 4.6».
6. ✅ Hypothesis-conflict блокировка работает (manual test).

---

## Если застрял

- ORM кросс-сервисный merge сложен → ограничь scope первой итерации:
  только Person + Events. Hypotheses/relationships → follow-up Phase 4.6.1.
- Hypothesis ORM не готов в main к моменту твоего merge → degrade
  gracefully: skip hypothesis-conflict check, добавь TODO + логируй.
- Confused про survivor selection → дефолт «оставить левую колонку»
  - UI toggle. Не overthink.

Удачи.
