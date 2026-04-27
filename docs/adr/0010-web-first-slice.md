# ADR-0010: Web stack & first slice (Phase 4.1)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Authors:** @autotreegen
- **Tags:** `web`, `frontend`, `phase-4`

## Контекст

Backend `parser-service` (Phase 3 + 3.1) — рабочий: GEDCOM-импорт, async
SQLAlchemy, события и participants, REST endpoints (`/imports`, `/trees/{id}/persons`,
`/persons/{id}`, `/healthz`). Реальный GED уже залит — 61k персон, 70k имён,
26k семей, 151k событий. Дальше из ROADMAP § 8 идёт «Phase 4 — Веб-сайт MVP»,
который описан широко: лендинг, auth, dashboard, импорт, граф дерева, i18n.

Сразу пилить весь Phase 4 одним PR-куском — слишком большой шаг. Нужен
минимальный «первый видимый продукт»: страница, которая через тот же API,
который уже работает, рендерит список персон и карточку. Без auth, без
mutations, без графа — только read-only. Этот ADR фиксирует выбор стека,
объём «первого слайса» и явно отложенные решения, чтобы будущие PR не
разъезжались по архитектуре.

`apps/web/` ещё не существует как полноценный workspace member: есть только
`.gitkeep`. Параллельно есть `apps/landing/` (статичный лендинг для
Cloudflare Pages, `output: "export"`) — он остаётся, его не трогаем.

## Рассмотренные варианты

### Вариант A — Next.js 15 App Router + TanStack Query (рекомендую)

Новый workspace member `apps/web/` (`@autotreegen/web`), стек как зафиксирован
в ADR-0001 (Next.js 15, React 19, TS strict, Tailwind 4, shadcn/ui).
Data-fetching через TanStack Query v5 поверх ручного `fetch`, типы вручную
скопированы из `services/parser-service/src/parser_service/schemas.py`.
Без auth, без mutations.

- ✅ Полностью совпадает с ADR-0001 (никаких новых решений).
- ✅ App Router + Server Components — идиоматичный путь Next 15; одинаковая
  модель и для дальнейших фаз (auth через middleware, SSR-стрим деталей
  персон, и т.д.).
- ✅ TanStack Query v5 закрывает сейчас то, что нужно (cache, retry,
  pagination, devtools), и одинаково удобен после введения auth и mutations
  (Phase 4.2 / 4.3) — менять инструмент не придётся.
- ✅ Ручные типы — короткий и очевидный путь для трёх endpoint'ов; OpenAPI
  codegen окупится, когда endpoint'ов станет 10+ (Phase 4.2).
- ❌ Дублирование Pydantic-схем в TS — пока endpoint'ов мало, цена низкая;
  на росте API становится проблемой.

### Вариант B — Next.js Pages Router

Тот же Next.js, но классический `pages/` API.

- ✅ Привычно тем, кто писал Next до 13.
- ❌ Legacy с т.з. Next 15 — Vercel и docs толкают всех на App Router;
  Server Components / streaming / parallel routes — нативно только в App Router.
- ❌ Через 1–2 фазы пришлось бы мигрировать всё равно. Технический долг
  на ровном месте.

### Вариант C — Remix / SvelteKit / TanStack Start / другое

Альтернативный фреймворк.

- ✅ В каждом есть свои сильные стороны (например, Remix loaders / form actions
  идиоматичнее для CRUD).
- ❌ Прямо нарушает ADR-0001 («Frontend: Next.js 15»). Чтобы менять стек, нужен
  отдельный ADR-сменщик с веским обоснованием — у нас его нет.
- ❌ shadcn/ui, Tailwind 4 best-practices, экосистема туториалов — всё больше
  завязано на Next App Router; уход с него = больше «делать руками».

## Решение

Выбран **Вариант A** для Phase 4.1. Конкретно:

- `apps/web/` — pnpm workspace member, имя пакета `@autotreegen/web`.
- Next.js 15 App Router, React 19, TypeScript strict, Tailwind 4 (`@theme`-блоки),
  shadcn/ui, Biome для lint+format (без ESLint/Prettier — повторяем
  `apps/landing/`).
- Server Components по умолчанию; client islands — только страницы, которым
  нужен TanStack Query (`/trees/[id]/persons`, `/persons/[id]`).
- Data-fetching: TanStack Query v5 + ручной `fetch`-обёртка
  (`apps/web/src/lib/api.ts`) с TypeScript-типами, скопированными из
  `services/parser-service/src/parser_service/schemas.py`. `queryKey`-структура —
  `['persons', treeId, { limit, offset }]`, `['person', personId]`.
- Без auth — API локально открыт (`http://localhost:8000`). Endpoint берётся
  из `process.env.NEXT_PUBLIC_API_URL` с дефолтом на localhost.
- Без mutations / редактирования.
- Без графической визуализации дерева (D3 / react-flow) — это Phase 4.4.
- Без i18n (русская локаль) — Phase 4.5.
- Mobile-first responsive — желательно, но не блокирующее: брейкпоинты
  Tailwind по умолчанию, без отдельной мобильной верстки.

В рамках Phase 4.1 в `apps/web/` появляются страницы:

1. `/` — placeholder («AutoTreeGen — coming soon»), чтобы корневой роут
   рендерился.
2. `/trees/[id]/persons` — пагинированный список персон, query params
   `?offset=`, лимит фиксированный (50). Карточка персоны → ссылка на
   `/persons/[id]`.
3. `/persons/[id]` — карточка с `primary_name`, списком имён и событий,
   кнопкой «Back to tree».

CORS на parser-service: разрешить `http://localhost:3000` для GET (см.
`fastapi.middleware.cors.CORSMiddleware`). Это правка backend'а, она
делается отдельным коммитом в ходе Phase 4.1.

## Последствия

**Положительные:**

- Первый видимый продукт — клик и вижу 61k реальных персон. Замыкаем
  цикл «backend → API → UI» через данные владельца.
- Никаких новых архитектурных решений за пределами ADR-0001/0002. Phase 4.1
  не переносит риска на Phase 4.2+.
- TanStack Query кеш и devtools покрывают навигацию без перезапросов
  (открыл персону → вернулся в список — данных уже есть).

**Отрицательные / стоимость:**

- Ручные TS-типы для трёх endpoint'ов — поддерживать в синхроне со
  `schemas.py` руками. Меняется редко, но каждое изменение требует
  обновления `apps/web/src/lib/api.ts`. Рост этой стоимости — триггер
  для введения OpenAPI codegen (см. ниже).
- Frontend сейчас не покрыт CI (`scripts/check.{ps1,sh}` гонят только
  `uv run`-команды). Pre-commit для frontend ограничен Biome через
  локальный `pnpm` (если он установлен). До Phase 4.2 frontend проверяется
  локально (`pnpm -F @autotreegen/web typecheck`, `pnpm lint`,
  `pnpm -F @autotreegen/web build`), а CI остаётся Python-only — это явный
  TODO для Phase 4.2.
- При появлении auth (Phase 4.2) нужно будет добавить middleware и
  `cookies()` API; сейчас провайдер `QueryClient` находится в
  `apps/web/src/app/providers.tsx`, и расширение цепочки провайдеров —
  тривиальное.

**Риски:**

- CORS на parser-service — точечный конфиг под `localhost:3000`. На проде
  это станет policy на Cloud Run / API Gateway; сейчас изменение точечное.
- React 19 + Next 15 + shadcn/ui — все на bleeding edge. Уже валидировано
  на `apps/landing/`, риск низкий.
- TanStack Query кеш на 61k персон — без виртуализации списков рендер
  страницы остаётся в пределах 50 элементов (пагинация ограничивает); если
  позже захотим infinite scroll, потребуется `useInfiniteQuery` +
  виртуализация (например, `@tanstack/react-virtual`).

**Что нужно сделать в коде:**

1. `pnpm create next-app apps/web` (TS, Tailwind, App Router, src/, alias `@/*`,
   без ESLint).
2. `apps/web/package.json` → `name: "@autotreegen/web"`.
3. Удалить дефолтные ESLint configs; biome из root наследуется через
   `apps/web/biome.json` (`extends: ["../../biome.json"]` как в
   `apps/landing/`).
4. Tailwind 4 setup — повторить паттерн `apps/landing/` (PostCSS plugin
   `@tailwindcss/postcss`, `@theme`-блоки в `globals.css`).
5. `pnpm -F @autotreegen/web add @tanstack/react-query @tanstack/react-query-devtools`.
6. `apps/web/src/lib/api.ts` — типы + `fetchPersons(treeId, limit, offset)`,
   `fetchPerson(personId)`, опциональный `fetchTree(treeId)`.
7. `apps/web/src/app/providers.tsx` — `QueryClientProvider` + `QueryDevtools`.
8. Страницы: `app/page.tsx`, `app/trees/[id]/persons/page.tsx`,
   `app/persons/[id]/page.tsx`.
9. shadcn/ui init + добавить `card`, `button`, `skeleton`, `badge`, `separator`.
10. parser-service: `CORSMiddleware(allow_origins=["http://localhost:3000"], allow_methods=["GET", "POST"])`.
11. `scripts/check.{ps1,sh}` — TODO в Phase 4.2 добавить `pnpm`-проверки;
    `tests/test_ci_parity.py` парсит только `uv run` команды, поэтому
    добавление pnpm-команд в локальные скрипты сейчас тест не сломает —
    но и в CI не попадает, что и есть оставшийся долг.

## Когда пересмотреть

- При переходе к auth (Phase 4.2): пересмотреть, нужна ли SSR-стрим
  страниц или достаточно client-side TanStack Query.
- При появлении 5+ новых endpoint'ов: ввести OpenAPI codegen (FastAPI
  уже отдаёт `/openapi.json`), переключить `apps/web/src/lib/api.ts` на
  сгенерированные типы и клиент.
- При переходе к мобильной версии: пересмотреть архитектуру layouts
  (адаптивные брейкпоинты vs отдельный bundle).
- При появлении графического дерева (Phase 4.4): возможно потребуется
  WebGL-рендерер (`pixi.js` / `regl`) — пересмотреть выбор библиотеки.
- Если frontend CI остаётся за бортом дольше Phase 4.2 — это сигнал
  пересмотреть приоритет (тест-парный инвариант ADR-0008 теряет смысл,
  пока половина проверок только локальная).

## Ссылки

- Связанные ADR: ADR-0001 (tech-stack), ADR-0002 (monorepo-structure),
  ADR-0008 (CI/pre-commit parity).
- ROADMAP § 8 (Phase 4 — Веб-сайт MVP).
- Brief: `docs/agent-briefs/phase-4-1-web-tree-view.md`.
- API endpoints: `services/parser-service/README.md`.
