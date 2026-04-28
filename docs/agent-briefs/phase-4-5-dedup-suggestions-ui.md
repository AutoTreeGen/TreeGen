# Agent brief — Phase 4.5: Dedup suggestions UI (review-only)

> **Кому:** Агент 1 — после Phase 4.3 (tree viz).
> **Worktree:** используй `TreeGen-task3` или новый.
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

Агент 2 закрыл Phase 3.4 — есть `GET /trees/{id}/duplicate-suggestions?entity_type=...`
который возвращает pairs с `confidence`, `components`, `evidence`.

Phase 4.5 — **UI для review этих suggestions**.

**КРИТИЧНО (CLAUDE.md §5):** auto-merge запрещён. UI только показывает
suggestions + позволяет user'у пометить "same" / "not same" / "skip".
Сам merge — ручной (Phase 4.6) через explicit confirmation.

**Параллельно работают:**

- Агент 2: новая Phase 7.1 (inference rules)
- Агент 3: Phase 5.1 (FS import)
- Агент 4: новая Phase 6.2 (DNA service)
- Агент 5: Phase 1.x gedcom (Task 3-4)
- Агент 6: Phase 7.0 (Task 2-3)

**Твоя территория:**

- `apps/web/src/app/trees/[id]/duplicates/page.tsx` (новая)
- `apps/web/src/lib/api.ts` — расширить (добавить fetchDuplicateSuggestions)
- `apps/web/src/components/duplicate-pair-card.tsx` (новый)
- ADR-0018 — UI для review (если решишь нужно)

**Что НЕ трогай:**

- `apps/web/src/app/persons/`, `apps/web/src/app/trees/[id]/persons/` — Phase 4.1/4.3 stable
- Backend кроме fetch — endpoint уже готов

---

## Задачи

### Task 1 — feat(web): /trees/[id]/duplicates page

1. `git checkout main && git pull`
2. `git checkout -b feat/phase-4.5-dedup-ui`
3. Расширить `lib/api.ts`:

   ```typescript
   export type DuplicateSuggestion = {
     entity_type: "source" | "place" | "person";
     entity_a_id: string;
     entity_b_id: string;
     confidence: number;
     components: Record<string, number>;
     evidence: Record<string, unknown>;
   };

   export async function fetchDuplicateSuggestions(
     treeId: string,
     entityType: "person" | "source" | "place",
     minConfidence = 0.8,
   ) {
     const res = await fetch(
       `${API_BASE}/trees/${treeId}/duplicate-suggestions?entity_type=${entityType}&min_confidence=${minConfidence}`
     );
     return res.json() as Promise<{items: DuplicateSuggestion[]}>;
   }
   ```

4. `app/trees/[id]/duplicates/page.tsx`:
   - useQuery → fetchDuplicateSuggestions
   - Tabs: Persons | Sources | Places
   - Slider: min confidence (0.6 — 0.95)
   - Grid: `<DuplicatePairCard pair={s} />`
5. `components/duplicate-pair-card.tsx`:
   - Side-by-side: entity A vs entity B (через дополнительный fetch person details)
   - Confidence badge (green ≥0.95, yellow 0.8-0.95, gray <0.8)
   - Components breakdown (которые rules сработали)
   - 3 actions: **Mark as same** (TODO Phase 4.6), **Not duplicate**
     (TODO Phase 4.6 — store as "rejected pair"), **Skip** (no action)
   - Кнопки **disabled** в этой Phase — только labels "Coming in 4.6"
6. `pwsh scripts/check.ps1` зелёное.
7. Commit, push, PR со скриншотом.

### Task 2 — feat(web): link from tree page

`app/trees/[id]/persons/page.tsx`: добавь кнопку
`<Link href={`/trees/${id}/duplicates`}>Review duplicates ({count} pending)</Link>`
в header.

PR + скриншот.

---

## Что НЕ делать

- ❌ Auto-merge (CLAUDE.md §5)
- ❌ DELETE/UPDATE на persons/sources/places через UI — кнопки disabled
- ❌ Backend изменения кроме fetch
- ❌ `git commit --no-verify`

---

## Сигналы успеха

1. ✅ `/trees/[id]/duplicates?type=person` рендерит pairs
2. ✅ Confidence breakdown видна
3. ✅ Side-by-side comparison работает
4. ✅ Скриншоты в PR
5. ✅ CI green

Удачи. После этого — твой /schedule на 4.2 frontend CI parity (через 2 недели), опциональный descendants chart (через 1 неделю — если хочешь, /schedule).
