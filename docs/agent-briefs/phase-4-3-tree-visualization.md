# Agent brief — Phase 4.3: tree visualization (pedigree chart)

> **Кому:** Агент 1 (Claude Code CLI, bypass on) — продолжение после Phase 4.1.
> **Контекст:** Windows, `D:\Projects\TreeGen`, default branch `main`.
> **Worktree:** ты уже работал в `D:/Projects/TreeGen-task3` или
> `D:/Projects/TreeGen-fs-worktree` — используй тот же подход для изоляции.
> **Перед стартом:** `git checkout main && git pull`

---

## Контекст

После Phase 4.1 у тебя в `apps/web/`:

- `/trees/[id]/persons` — пагинированный список
- `/persons/[id]` — карточка с именами/событиями
- API client + TanStack Query

Phase 4.3 — **визуальное дерево**. Pedigree chart (предки) и/или
descendant chart (потомки). Это первый "wow"-момент для пользователя:
ты видишь свою семью графически.

**Параллельно работают:**

- Агент 2: `services/parser-service/` (Phase 3.3 sources)
- Агент 3: `packages/familysearch-client/` (Phase 5.0)
- Агент 4: `packages/dna-analysis/` (Phase 6.0 — почти done)

**Твоя территория:**

- `apps/web/` — целиком
- `services/parser-service/src/parser_service/api/trees.py` — может понадобиться
  новый endpoint `/persons/{id}/ancestors?generations=N` (read-only, не
  меняет existing)
- `services/parser-service/src/parser_service/schemas.py` — может
  понадобиться `AncestorTree` схема
- `docs/adr/0013-*.md` — новый ADR
- `scripts/check.{ps1,sh}` — добавить web-build шаги если ещё не там
  (может ты уже сделал в 4.1)
- `.github/workflows/ci.yml` — НЕ ТРОГАЙ (это Phase 4.2 follow-up,
  scheduled через 2 недели)

**Что НЕ трогай:**

- `services/parser-service/services/import_runner.py` (Агент 2)
- `services/parser-service/api/*.py` кроме `trees.py` (тоже возможно Агент 2)
- `packages/familysearch-client/`, `packages/dna-analysis/`,
  `packages/shared-models/orm.py`

---

## Цель Phase 4.3

1. **`/persons/[id]/ancestors` endpoint** в API — возвращает дерево предков
   (рекурсивный CTE на N поколений) одним запросом
2. **Frontend page `/persons/[id]/tree`** — рендерит pedigree chart:
   - Анкер (selected person) в центре/слева
   - До 5-6 поколений предков
   - Каждый узел: фото-плейсхолдер + имя + годы жизни
   - Клик на узле → переход к `/persons/[other-id]/tree`
   - Loading skeleton, empty state если нет родителей
3. **Минимум интерактива:**
   - Pan + zoom (touch/mouse)
   - Highlight на hover
   - Бейдж "DNA tested" если у персоны есть `dna_results` (Phase 6
     зайдёт позже, пока стаб)

---

## Задачи (в этом порядке)

### Task 1 — docs(adr): ADR-0013 tree visualization tech choice

**Цель:** зафиксировать выбор библиотеки до кода.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b docs/adr-0013-tree-visualization`
3. Создать `docs/adr/0013-tree-visualization.md`:
   - Status: Accepted, Date: today, Authors: @autotreegen
   - Tags: web, frontend, visualization, phase-4
   - Контекст: нужен интерактивный pedigree chart
   - Рассмотренные варианты:
     - **A. React Flow** (popular, declarative, нативный pan/zoom,
       но больше для DAG'ов, не специфичен для деревьев)
     - **B. D3 + custom React wrapper** (полный контроль,
       но больше boilerplate, сам реализуешь pan/zoom)
     - **C. react-d3-tree** (готовая библиотека для tree-layouts,
       declarative, но менее настраиваемая чем чистый D3)
     - **D. SVG + custom code** (zero deps, но всё сам)
   - Решение: **C (react-d3-tree)** для MVP — быстро, declarative,
     потом мигрировать на B если нужна тонкая настройка
   - Trade-offs: размер bundle (~50KB), customization limits, etc
   - Когда пересмотреть: при миллионе узлов (нужен canvas/WebGL),
     при сложных циклах в дереве (DNA matches без иерархии)
4. `pwsh scripts/check.ps1` — зелёное (только pre-commit на docs)
5. Commit, push, PR
6. Дождаться зелёного CI, мерджить

### Task 2 — feat(api): /persons/{id}/ancestors endpoint

**Цель:** API возвращает рекурсивное дерево предков.

**Шаги:**

1. `git checkout main && git pull`
2. `git checkout -b feat/phase-4.3-ancestors-endpoint`
3. В `services/parser-service/src/parser_service/api/trees.py`
   добавить endpoint `GET /persons/{person_id}/ancestors?generations=N`:
   - Дефолт generations=5, max=10
   - Использует SQLAlchemy с recursive CTE на FamilyChild → Family →
     husband_id/wife_id, итерируя N раз
   - Возвращает `AncestorTreeNode` (новый Pydantic schema):

     ```python
     class AncestorTreeNode(BaseModel):
         id: uuid.UUID
         primary_name: str | None
         birth_year: int | None
         death_year: int | None
         sex: str
         father: "AncestorTreeNode | None" = None
         mother: "AncestorTreeNode | None" = None
     ```

4. Тест `test_ancestors_returns_tree_structure`:
   - после import _MINIMAL_GED (extend если нужно: bigger family tree)
   - GET /persons/`<root>`/ancestors?generations=3
   - response.father.primary_name == expected
   - response.mother == None (если в фикстуре нет матери)
5. **Внимание:** запрос может вернуть **много** узлов (2^5 = 32 в
   полном случае). Тестируй performance на 5-6 поколениях.
6. `pwsh scripts/check.ps1` зелёное
7. Commit, push, PR
8. Мерджить после зелёного CI

### Task 3 — feat(web): /persons/[id]/tree page with react-d3-tree

**Цель:** видимое дерево предков.

**Шаги:**

1. `feat/phase-4.3-web-tree-page`
2. Установить:

   ```text
   pnpm -F @autotreegen/web add react-d3-tree
   ```

3. Создать `apps/web/src/app/persons/[id]/tree/page.tsx`:
   - useQuery → `fetch(/persons/${id}/ancestors?generations=5)`
   - Конвертить `AncestorTreeNode` → формат react-d3-tree (`{name, attributes, children}`)
   - Render `<Tree data={...} orientation="horizontal" pathFunc="step" ...>`
   - Custom node renderer (foreignObject + Tailwind):
     - Имя bold
     - Годы dimmed
     - Sex-icon (♂/♀/⚧)
     - Hover effect
     - Click → router.push(`/persons/${id}/tree`)
4. Loading skeleton + error state
5. Pan + zoom: react-d3-tree поддерживает out-of-box (`zoomable`)
6. Тест: `pnpm -F @autotreegen/web build` — должно собраться
7. `pwsh scripts/check.ps1` — должно быть зелёное (включая pnpm build)
8. Скриншот в PR description (открой `localhost:3000/persons/<id>/tree`,
   снимок)
9. Commit, push, PR

### Task 4 — feat(web): link from persons list / detail

**Цель:** навигация — "View family tree" кнопка.

**Шаги:**

1. `feat/phase-4.3-web-tree-link`
2. На `/persons/[id]` (карточка) добавить кнопку
   `<Link href={`/persons/${id}/tree`}>View family tree</Link>`
3. На `/trees/[id]/persons` — на каждой карточке персоны добавить
   маленький button "Tree" → ссылка на /persons/[id]/tree
4. Тест: build green
5. `pwsh scripts/check.ps1` зелёное
6. Commit, push, PR со скриншотами обеих страниц с кнопками

### Task 5 (опционально) — feat(web): descendant tree (потомки)

Если время есть, аналогично сделать `/persons/[id]/descendants`
endpoint + страница `/persons/[id]/descendants` с descendant chart.

---

## Что НЕ делать

- ❌ **Editing** дерева (drag-drop, добавление родителей) — Phase 4.5
- ❌ **DNA cluster overlay** — Phase 6.x когда DNA matching будет
- ❌ **Print/PDF export** — отдельная feature
- ❌ **Mobile-optimized layout** — это nice-to-have, но не блокер
- ❌ Трогать backend кроме `api/trees.py` (новый endpoint) и
  `schemas.py` (новый Pydantic)
- ❌ `.github/workflows/ci.yml` — это Phase 4.2 follow-up через
  2 недели (твой /schedule)
- ❌ `git commit --no-verify`
- ❌ Мердж с красным CI

---

## Сигналы успеха

После 4 PR:

1. ✅ `GET /persons/{id}/ancestors?generations=N` возвращает дерево
2. ✅ `localhost:3000/persons/<id>/tree` рендерит pedigree
3. ✅ Pan+zoom работает
4. ✅ Click на узле → переход
5. ✅ Все CI зелёные
6. ✅ ADR-0013 в `docs/adr/`
7. ✅ Скриншоты дерева в PR descriptions

---

## Hint: типичные размеры

- Pedigree чарт 5 поколений = до 31 узлов (1+2+4+8+16) → ~0.5 сек на API
- 6 поколений = до 63 узлов → 1 сек
- 10 поколений = до 1023 узлов → начинают тормозить, лучше lazy-load

Для MVP лимит 5-6 поколений по умолчанию + позволить override через query param.

---

## Coordination

- Если корневой `pyproject.toml` / `uv.lock` конфликтует — rebase + uv lock
- `services/parser-service/api/trees.py` — Агент 2 потенциально может
  править (Phase 3.3 Task 4: enrich person events с citations/media).
  Если конфликт — взять обе стороны: его citations/media + твой
  ancestors endpoint
- `schemas.py` — аналогично, добавить новые модели не пересекаясь

Удачи. Жду PR-ссылок и скриншот pedigree chart.
