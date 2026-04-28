# ADR-0041: Frontend error handling — boundaries, retry, offline UX

- **Status:** Accepted
- **Date:** 2026-04-29
- **Authors:** @autotreegen
- **Tags:** `frontend`, `error-handling`, `offline`, `phase-4.6`

## Контекст

Phase 4.x наполнил frontend «реальными» страницами: pedigree-tree
(D3, тяжёлый layout), DNA chromosome painting, hypotheses review,
sources viewer. До Phase 4.6 любая ошибка в любом из них роняла
весь React-tree до white screen — без объяснения, без recovery, без
локализованного сообщения.

Параллельно Phase 4.10 (Clerk auth) и Phase 12.0 (Stripe) добавили
HTTP-вызовы, которые могут возвращать 401/402/422/5xx. Существующий
``getJson`` бросал generic ``ApiError(status, message)`` — caller'у
негде было решить «retry это или показать ValidationError».

Этот ADR фиксирует pattern на три фронта одновременно:

1. **React error boundaries** — global + per-route, чтобы падение
   одной секции не валило весь app.
2. **Typed API errors + retry** — иерархия ``ApiError`` →
   ``NetworkError`` / ``AuthError`` / ``ValidationError`` /
   ``ServerError`` + ``withRetry`` с exponential backoff.
3. **Offline UX** — banner + reconnect-fetch + IndexedDB action queue
   stub.

## Рассмотренные варианты

### A. Только global ErrorBoundary

- ✅ Меньше кода, один fallback на всё.
- ❌ Падение D3 на /trees/[id] валит header, /dna, /sources — user
  теряет контекст и не понимает, какой раздел сломался.
- ❌ Нельзя восстановить раздел отдельно (resetErrorBoundary
  re-mount'ит ВСЁ дерево, включая успешно-работавшие части).

### B. Global + per-route boundaries (выбрано)

- ✅ Падение секции локализовано — header работает, /persons
  работает, broken /trees показывает inline-error.
- ✅ resetErrorBoundary секции повторяет render именно сломавшегося
  поддерева, не валит весь page.
- ✅ Per-route fallback видит контекст (URL, navigation), может
  предложить «вернись на /trees» вместо «back to home».
- ❌ +5 layout.tsx файлов (по одному на каждую критическую секцию).
  Acceptable cost для значимого UX-выигрыша.

### C. global + per-component boundaries (одна обёртка вокруг

каждого тяжёлого компонента: PedigreeTree, ChromosomePainting, ...)

- ✅ Максимальная гранулярность.
- ❌ Boundaries загромождают call-site'ы каждого компонента.
- ❌ Unclear, на каком уровне «section vs component» — где провести
  черту? Если автор-компонента забыл обернуть — silent regression.
- ❌ Вместо одного места, где проверять «секция изолирована»,
  получаем разбросанное знание.

Вариант B — sweet spot: per-route layout даёт явный border, route'ы
определены по domain (trees/dna/sources/hypotheses/persons), и когда
появится новый раздел, его layout.tsx — естественное место добавить
boundary.

## Решение

### 1. ErrorBoundary

Используем **react-error-boundary v6**. Аргументы:

- Maintained, React 19 ready.
- Декларативный API (`<ErrorBoundary FallbackComponent={...}>`) против
  raw class component.
- ``onError`` hook для logging / Sentry-sink (Phase 13.x).
- ``resetErrorBoundary`` props в fallback — caller сам решает, что
  ресетить.

Два варианта обёртки:

- ``GlobalErrorBoundary`` — обёртывает root content (после
  ``<SiteHeader>``, чтобы header выжил при крэше). Full-page
  fallback.
- ``SectionErrorBoundary`` — на route layout'е. Inline-fallback
  в content area; header / sidebar / другие route'ы продолжают
  работать.

Per-route boundaries вешаются на:

- `/trees/*` — pedigree D3, big-tree memory.
- `/dna/*` — chromosome painting (тяжёлый SVG), match list.
- `/sources/*` — citation graph.
- `/hypotheses/*` — review UI.
- `/persons/*` — person detail + ancestors fetch.

Fallback показывает:

- i18n заголовок + body (`errors.globalTitle` / `errors.sectionTitle`).
- ``<pre>`` с `error.name: error.message` — без stack trace
  (security: stack может содержать internal paths).
- «Try again» — ``resetErrorBoundary``.
- «Report issue» — `mailto:support@autotreegen.com` с pre-filled
  subject + body (error name + message + URL). Phase 13.x: заменим
  на in-app feedback widget со sentry-integration.
- Global-only: «Back to home» link (когда whole app сломан).

### 2. Typed API errors + retry

Иерархия:

```text
ApiError (base)
├─ NetworkError    — fetch не дошёл (TypeError, abort, offline)
├─ AuthError       — 401 / 403
├─ ValidationError — 4xx (кроме 401/403)
└─ ServerError     — 5xx
```

`classifyHttpError(status, message)` маппит HTTP status в правильный
subclass. `isRetryableError(err)` → true только для
``NetworkError`` + ``ServerError``.

`withRetry(fn, opts)` — exponential backoff:

- `maxAttempts` default 3.
- Delay = `baseDelayMs * 2^(attempt-1)` с jitter ±25%.
- Sleep injection через `opts.sleep` для тестов (без таймеров).

`getJson` (idempotent GET path) автоматически оборачивается в
`withRetry`. Non-idempotent (POST/PATCH/DELETE) call-site'ы используют
`fetchOnceJson` — caller сам решает retry-policy.

**401 handler.** `setUnauthorizedHandler(() => void)` позволяет
DI-style подключить редирект (по умолчанию `window.location.assign("/sign-in")`).
В тестах и при появлении Clerk auth (Phase 4.10) handler заменяется
на Clerk's `signOut` + redirect.

### 3. Offline UX

`OfflineIndicator` — sticky banner:

- Слушает `online` / `offline` события + `navigator.onLine` на mount.
- При offline: показывает banner с i18n-сообщением.
- При online: invalidates все react-query queries
  (`queryClient.invalidateQueries()`) → UI re-fetch'ится со свежими
  данными.

`offline-queue.ts` — IndexedDB action queue (через `idb-keyval`):

- `enqueue(action)` — кладёт `{path, method, body}` в очередь.
- `listQueue()` / `clearQueue()`.
- **STUB в Phase 4.6** — actual flush делается Phase 4.7 после того,
  как mutating endpoint'ы появятся в UI. Сейчас в UI только read-flow,
  очередь вызывается редко.

### 4. Service worker

`/sw.js` — минимальный кэш static assets:

- Pre-cache: `/`.
- Fetch handler: `_next/static/*` и `/static/*` → stale-while-revalidate.
- **API responses не кэшируются.** Provenance-first приложение хуже
  переносит stale tree data, чем offline screen.

Регистрация — только в production (`process.env.NODE_ENV === "production"`).
В dev'е HMR конфликтует с sw cache'ем.

## Последствия

**Положительные:**

- Падение одной секции не валит весь app — UX retention выше.
- Typed errors дают call-site'у инструмент для разных UI на разных
  failure modes (retry для 5xx, redirect для 401, inline для 422).
- Offline banner — first-class signal вместо silent network error.
- `withRetry` устраняет stand-alone retry-loops в каждой call-site.

**Отрицательные / стоимость:**

- +5 `layout.tsx` файлов на critical routes.
- +2 dependencies (`react-error-boundary`, `idb-keyval`). Bundle weight
  ~2 КБ + ~1.5 КБ (gzipped).
- Каждый разработчик нового раздела должен помнить про SectionErrorBoundary
  в его layout. Mitigation: code-review checklist + ADR.

**Риски:**

- Service worker может cache'ировать stale builds (известная Next.js
  проблема). Mitigation: cache name versioning (`autotreegen-static-v1`),
  `activate` event удаляет старые caches.
- Boundary catches только render-time errors; async errors в effect'ах
  (uncaught promise rejections) не ловятся. Phase 13.x: `window.onunhandledrejection`
  hook + Sentry sink.
- `withRetry` jitter использует `Math.random()` — не cryptographic;
  acceptable для backoff (не нужен).

## Когда пересмотреть

- Если bundle size станет критичным — заменить `idb-keyval` на
  inline IndexedDB code (~30 строк).
- Если требования усилятся (offline-first для tree edits) — переписать
  `offline-queue.ts` на `Workbox` + background sync.
- Когда Sentry приедет (Phase 13.x) — `logBoundaryError` отправляет
  через `Sentry.captureException(error, { contexts: { react: info } })`.

## Ссылки

- ADR-0024 / ADR-0029 — backend notification model (для дифференциации
  in-app vs email vs error-boundary).
- ADR-0034 — payments (402 — separate UX, не error-boundary surface).
- react-error-boundary v6 — <https://github.com/bvaughn/react-error-boundary>.
