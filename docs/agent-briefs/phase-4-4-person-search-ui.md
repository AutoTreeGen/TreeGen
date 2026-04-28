# Agent brief — Phase 4.4: Person search UI

> **Кому:** Claude Code CLI с `--dangerously-skip-permissions` (bypass on).
> **Контекст:** Windows, `D:\Projects\TreeGen`. Используй worktree
> `../TreeGen-search` чтобы не пересекаться с другими агентами в `apps/web`.
> Перед стартом прочитай: `CLAUDE.md`, `ROADMAP.md` §7.4 (или ближайшая
> секция про web), `docs/adr/0010-web-stack.md`, `docs/agent-briefs/phase-4-1-web-tree-view.md`.

---

## Зачем

После Phase 4.1 у нас есть `/trees/[id]/persons` — пагинированный список.
Это работает на 100 человек, но мой реальный GED уже 12 000+ персон.
Нужен **поиск по имени** (минимум) и **фильтр по году рождения** (как
быстрый второй шаг). Это первая фича, которую владелец будет реально
использовать каждый день.

Не auto-merge, не фантазии — простой текстовый поиск + диапазон лет.
LLM/embeddings не используем (ещё не нужны на этом этапе, см. ADR-0010).

---

## Что НЕ делать

- ❌ Семантический поиск (embeddings) — отдельный issue, не в этом скоупе.
- ❌ Транслитерация / Daitch-Mokotoff в UI — это бэкенд, мы сюда подключим
  в Phase 4.6 или 7.x. Сейчас — голый ILIKE.
- ❌ Фасетный фильтр по местам — Phase 4.4.x, отдельный PR.
- ❌ Авто-комплит на каждом keystroke — debounce 300 мс минимум, уважаем
  свой бэкенд.
- ❌ `git commit --no-verify`. Pre-commit должен пройти.
- ❌ Прямые коммиты в `main`. Только PR.

---

## Задачи (в порядке)

### Task 1 — Backend: search endpoint

**Цель:** `GET /trees/{tree_id}/persons/search?q=&birth_year_min=&birth_year_max=&limit=&offset=`
возвращает пагинированный список Person.

**Шаги:**

1. `git checkout main && git pull`
2. `git worktree add ../TreeGen-search -b feat/phase-4.4-person-search`
3. `cd ../TreeGen-search`
4. В `services/parser-service/src/parser_service/api/persons.py` (или где
   живёт persons-роутер — найди через `grep -r "trees.*persons" services/parser-service`)
   добавь роутер `GET /trees/{tree_id}/persons/search`. Параметры:
   - `q: str | None` — substring (case-insensitive) по `given_name` ИЛИ
     `surname` ИЛИ их конкатенации; ILIKE `%q%`. Если пусто — без фильтра.
   - `birth_year_min: int | None`, `birth_year_max: int | None` —
     фильтр по году из birth event (если такой связи ещё нет —
     используй `Person.birth_year_estimated` или подобное; если поля
     нет совсем — JOIN на events таблицу с `event_type='BIRT'` и
     извлеки год из `date` jsonb-поля).
   - `limit: int = 50` (max 200), `offset: int = 0`.
5. Response shape — переиспользуй существующий `PersonListItem` из
   `/trees/{id}/persons`. Добавь поле `total: int` в обёртке если ещё нет.
6. Тесты в `services/parser-service/tests/test_persons_search.py`:
   - empty `q` → возвращает всех (с пагинацией).
   - `q=Zhit` (case-insensitive) → находит Zhitnitzky.
   - `birth_year_min=1850&birth_year_max=1900` → фильтр работает.
   - комбинация `q` + год — AND.
   - 404 если `tree_id` не существует.
   - SQL-injection попытка (`q=' OR 1=1--`) — не валится, не возвращает
     лишнего (SQLAlchemy parameterizes — это автотест).
7. `uv run pytest services/parser-service/tests/test_persons_search.py -v`
   → green.

### Task 2 — Frontend: search page

**Цель:** `/trees/[id]/persons` получает search-инпут и фильтр годов
(или, если хочешь чище, отдельная страница `/trees/[id]/search`).
Решай сам — главное, чтобы юзер из списка персон мог искать.

**Шаги:**

1. В `apps/web/src/app/trees/[id]/persons/page.tsx` (если такой путь —
   Phase 4.1 структура; адаптируй если иначе) добавь:
   - `<Input>` (shadcn) для `q`, debounce 300 мс через `useDebouncedValue`
     или `setTimeout` в `useEffect`.
   - Два `<Input type="number">` для `birth_year_min` / `birth_year_max`,
     либо `<Slider>` если хочется (но Input проще на MVP).
   - `<Button variant="ghost">Clear</Button>` сбрасывает все три.
   - URL state: `q`, `birth_year_min`, `birth_year_max`, `page` в search
     params (через Next.js `useSearchParams` + `router.replace`), чтобы
     URL шарился и работал back-кнопкой.
2. Fetch функция — на server component если возможно (Next 15 RSC),
   иначе client с `useEffect`. Endpoint — Task 1.
3. Empty state: «Nothing found for `<q>`» с предложением сбросить фильтры.
4. Loading state: skeleton rows (используй `<Skeleton>` из shadcn).
5. Error state: красная плашка «Search failed: `<message>`» + retry.
6. Тесты компонентов — Vitest или Playwright snapshot, на твой выбор;
   минимум 1 happy-path тест что инпут отправляет запрос с правильным
   query string. Если frontend test infra ещё хрупкая — допустимо
   ограничиться backend coverage и manual QA.

### Task 3 — Документация и финал

1. Обнови `ROADMAP.md` — отметь 4.4 как done с датой и ссылкой на PR.
2. Если backend search field design не очевиден (где хранится
   `birth_year`) — короткая заметка в `docs/data-model.md` (или в комменте
   к роутеру) почему именно так.
3. `pwsh scripts/check.ps1` — green.
4. Commit (БЕЗ `--no-verify`), push, PR `feat/phase-4.4-person-search`.
5. PR description: что делает, какие edge cases покрыты, скриншот UI
   (drag-drop в GitHub PR — стандартно).
6. Дождись зелёного CI. Если красный — итерация до зелёного.

---

## Сигналы успеха

1. ✅ `GET /trees/{id}/persons/search` работает, тесты зелёные.
2. ✅ UI с поиском + фильтром годов в `/trees/[id]/...`, debounce работает.
3. ✅ URL-state — поделиться ссылкой `?q=Zhit&birth_year_min=1850` работает.
4. ✅ `pwsh scripts/check.ps1` green.
5. ✅ PR merged, CI green, ROADMAP обновлён.
6. ✅ На моём реальном GED (12k+ персон) поиск `Zhit` возвращает результат
    за < 500 мс (manual QA — приложи скриншот в PR).

---

## Если застрял

- Schema `Person` непонятна (где `birth_year`) → спроси меня в PR
  description, не угадывай.
- Frontend test infra падает на ровном месте → допустимо отложить
  frontend тесты в follow-up issue, главное — backend покрыт.
- Performance деградация на больших таблицах → добавь индекс
  `CREATE INDEX ON persons (tree_id, lower(surname))` в Alembic
  миграцию, упомяни в PR.

Удачи. Жду PR-ссылку.
