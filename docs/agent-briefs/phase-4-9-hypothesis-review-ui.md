# Agent brief — Phase 4.9: Hypothesis review UI

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Worktree `../TreeGen-hypothesis-ui`.
> Все prerequisites в main: Phase 7.2 (Hypothesis ORM + API), Phase 7.3
> (DNA-aware rules), Phase 4.5/4.6 (review/merge UI infrastructure),
> Phase 3.6 (sources). Готовая инфраструктура — нужен интерфейс ревью.
> Перед стартом: `CLAUDE.md` §3.2, `docs/adr/0021` (hypothesis persistence),
> `docs/adr/0023` (DNA-aware inference), существующие
> `apps/web/src/app/trees/[id]/duplicates/` и `.../persons/[id]/merge/`.

---

## Зачем

Сейчас гипотезы **генерируются** (Phase 7.x) и **сохраняются** (ORM),
но юзер их **не видит**. Это критическая дыра в workflow:

1. DNA inference rule создал «X — родственник Y, total cM 1500 →
   parent/sibling».
2. Hypothesis Persisted в БД с `status='pending_review'`.
3. **Тишина.** Юзер не узнаёт.

Phase 4.9 закрывает: список + деталь + actions (approve/reject/defer).
**No auto-merge** — вся логика manual review (CLAUDE.md §5).

---

## Что НЕ делать

- ❌ Не triggers compute прямо из UI (это Phase 7.5 endpoint, отдельно).
- ❌ Не auto-approve high-confidence hypothesis. Всегда ручной click.
- ❌ Не merge persons прямо отсюда — кнопка Approve выводит на Phase 4.6
  merge UI с pre-filled данными.
- ❌ Не показывать raw DNA segments (rsids/genotypes) — только
  агрегаты (chromosome, cM, total).
- ❌ `--no-verify`, прямой push в main.

---

## Задачи

### Task 1 — API endpoints (если ещё нет)

**Файл:** `services/parser-service/src/parser_service/api/hypotheses.py`
(Phase 7.2 уже создал базу — расширь).

```text
GET /trees/{tree_id}/hypotheses?status=pending&rule_id=...&confidence_min=...
                              &limit=50&offset=0
                              → пагинированный список с total
GET /hypotheses/{id}           → детали с full evidence breakdown
POST /hypotheses/{id}/review   body {action: 'approve'|'reject'|'defer',
                                     reason?: str, reviewer_user_id: int}
                              → обновляет status, возвращает updated record
```

Тесты в `tests/test_hypotheses_review_api.py` (3-5 happy-paths +
edge cases: review on already-reviewed, invalid action).

### Task 2 — List page

**Файл:** `apps/web/src/app/trees/[id]/hypotheses/page.tsx`

UI:

- Фильтры в URL state (как Phase 4.4 search):
  - Status: pending (default) | approved | rejected | deferred | all
  - Rule: dropdown (all rule_ids from API), default all
  - Confidence: slider 0–1, default 0.5+
- Таблица колонки:
  - Confidence (visual bar 0-1, color-coded: red <0.4, yellow 0.4-0.7, green 0.7+)
  - Rule (badge с rule_id)
  - Subject (link to person)
  - Predicate (e.g. "is parent-of" / "shares ancestor with")
  - Object (link to person/cluster)
  - Evidence count (badge "3 evidences")
  - Actions: «Review» button → detail page
- Pagination + "Showing N of M" counter.
- Empty state: "No pending hypotheses. Run compute via /compute-all
  endpoint or wait for next scheduled run."

### Task 3 — Detail page

**Файл:** `apps/web/src/app/hypotheses/[id]/page.tsx`

UI:

- Header: Hypothesis title + status badge + confidence meter
- Subject card (link to person profile)
- Object card (link to person/cluster)
- Predicate explanation (human-readable)
- **Evidence breakdown** (этот блок ключевой):
  - Для DNA evidence (`evidence_type='dna_segment_match'`):
    - List of segments: chromosome | start_bp | end_bp | cM
    - Total cM, total segments
    - Endogamy flag if set (badge "AJ-adjusted")
    - Visual: simple horizontal bar showing chromosome regions
      (можно SVG, можно simple divs flex-row)
  - Для source-citation evidence:
    - Source title (link к Phase 4.7 source viewer)
    - Page reference, QUAY badge
    - Optional text excerpt
  - Для name/date match evidence:
    - Side-by-side comparison
- **Actions row** (sticky bottom):
  - Approve button (primary, green) — opens confirm dialog
  - Reject (destructive, red) — opens dialog with reason field
  - Defer (secondary) — moves to deferred queue, stays in pending count
  - Open in merge UI (link to Phase 4.6 with pre-filled subject+object)
- Action history (если уже review был): «Approved by Owner on YYYY-MM-DD,
  reason: ...».

### Task 4 — Wire approve action to merge flow

При клике Approve на hypothesis типа «X is duplicate of Y»:

1. Confirm dialog: «Approving will mark this hypothesis approved AND
   open the manual merge UI. Proceed?»
2. POST /hypotheses/{id}/review {action: 'approve'}
3. Redirect to /persons/{X}/merge?target={Y}&from_hypothesis={hyp_id}

Phase 4.6 merge UI читает `from_hypothesis` query param и показывает
плашку «Merging based on hypothesis #N from rule X (confidence 0.85)».

### Task 5 — Notification integration (light)

При создании hypothesis с status=pending_review автоматически:

- В hypothesis_runner (services/parser-service/services/hypothesis_runner.py),
  после persist каждой новой гипотезы:
  - Вызвать notification-service POST /notify
    `{user_id: <tree owner>, event_type: 'hypothesis_pending_review',
      payload: {hypothesis_id: N, tree_id: M, confidence: 0.X}}`
- Если notification-service недоступен — log warning, не блокируй.

В UI на главной странице древа добавь маленький badge "N pending
hypotheses" с link на review queue (использует existing GET endpoint
с status=pending count).

### Task 6 — Финал

1. ROADMAP §4.9 → done.
2. `pwsh scripts/check.ps1` green.
3. PR `feat/phase-4.9-hypothesis-review-ui`.
4. CI green до merge. Никакого `--no-verify`.
5. PR description: скриншот list page + detail page + approve flow.
6. Manual QA waiting on owner: на real GED + DNA cohort открыть
   /trees/[id]/hypotheses — должны быть видны hypothesis от Phase 7.3.

---

## Сигналы успеха

1. ✅ /trees/[id]/hypotheses page работает с filters + pagination.
2. ✅ /hypotheses/[id] detail page показывает evidence breakdown
    (особенно DNA segments visualization).
3. ✅ Approve → Phase 4.6 merge UI flow работает с query param.
4. ✅ Notification fires при создании hypothesis (если notification-service up).
5. ✅ Pending count badge на /trees/[id] home page работает.

---

## Если застрял

- DNA segment visualization сложна → MVP = просто текст-лист segments,
  visualization в follow-up Phase 4.9.1.
- notification-service недоступен в dev → graceful degrade,
  warning в console, не блокируй UI.
- Cluster hypotheses (от Phase 7.4 которая ещё не написана) — пока
  игнорируй в filter, добавь когда придёт.
- ORM impedance с evidence_data jsonb → используй Pydantic discriminated
  union по `evidence_type` для type safety на API layer.

Удачи. Это закрывает критическую дыру workflow loop.
