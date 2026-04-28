# ADR-0025: Vitest как frontend test runner

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `web`, `frontend`, `testing`, `phase-4`

## Контекст

К концу Phase 4.3 (визуализация дерева) `apps/web/` имеет четыре страницы —
person list (4.4), pedigree tree (4.3), duplicates review (4.6), source viewer
(4.7) — и **ноль frontend-тестов**. Backend полностью покрыт `pytest` (>80%
на новой логике, ADR-0001), но веб-часть — слепая зона: каждая регрессия
ловится либо вручную в браузере, либо в проде.

Перед стартом Phase 4.4+ нужно поднять минимальную инфраструктуру тестов,
чтобы каждая следующая страница приходила с покрытием. Откладывать дальше
дороже: чем больше страниц без тестов, тем больше ретроактивных тестов
писать одним большим заходом, и тем выше вероятность, что pure-функции
вроде `toRawNode` (конверсия API → react-d3-tree) будут перепутаны
с UI-логикой.

Стек уже зафиксирован: Next.js 15 / React 19 / TS strict / pnpm (ADR-0001,
ADR-0010). Выбрать нужно только runner.

## Рассмотренные варианты

### Вариант A — Vitest + React Testing Library (рекомендую)

`vitest` как тест-раннер, `@vitejs/plugin-react` для JSX/TSX, `jsdom` как
environment, `@testing-library/react` + `@testing-library/jest-dom` +
`@testing-library/user-event` для DOM-ассертов и пользовательских
взаимодействий.

- ✅ Нативный ESM, нативный TypeScript — никакого `babel-jest` / `ts-jest`
  не нужно. Совпадает с тем, как Next 15 уже работает с TS.
- ✅ Совместимый с Jest API (`describe` / `it` / `expect` / `vi.fn()`),
  миграция знаний нулевая.
- ✅ Хорошо живёт с monorepo (`pnpm -F`), конфиг — один `vitest.config.ts`
  с алиасом `@/*` в зеркало `tsconfig.paths`.
- ✅ Watch-режим заметно быстрее Jest на больших workspace-ах
  (ESM + Vite-граф зависимостей).
- ❌ Меньше зрелого экосистемного контента (туториалов, рецептов) по
  сравнению с Jest, но для базовых сценариев (RTL + jsdom) разницы нет.

### Вариант B — Jest + ts-jest + jsdom

«Стандартный» рантайм для React-проектов до 2023.

- ✅ Самый большой объём готовых рецептов (Next.js docs, RTL docs).
- ❌ ESM + TypeScript всё ещё через flags / experimental — для проекта
  на чистом ESM это лишняя боль.
- ❌ Дополнительный transform-слой (`ts-jest` или `babel-jest`) — лишняя
  конфигурация, лишнее время старта.
- ❌ Не интегрирован с Vite-стилем resolve (которым уже пользуется Next 15
  под капотом через turbopack), нужен дублирующий конфиг алиасов.

### Вариант C — Next.js встроенная testing experimental APIs

У Next 15 есть `next/testing` для e2e/route-handler тестов.

- ✅ Прямая интеграция с App Router.
- ❌ Это про route-handler / server-component тесты, не про unit-тесты
  pure-функций и компонентов. Не закрывает основной use-case.
- ❌ Experimental — не хочется ставить эту зависимость для базового слоя.

### Вариант D — Playwright Component Tests

Полный браузер вместо jsdom, реальный canvas/ResizeObserver.

- ✅ Решает проблему с `react-d3-tree`, который завязан на canvas.
- ❌ Сильно тяжелее: запускает headless Chromium на каждый тест.
- ❌ Overkill для текущих pure-функций (`toRawNode`).
- ✅ Имеет смысл **позже** для e2e сценариев (Phase 4.x — auth flow, импорт),
  но не как базовый unit-runner.

## Решение

Выбран **Вариант A — Vitest + React Testing Library**.

`vitest run` подключается к pnpm-workspace через скрипт `test` в
`apps/web/package.json` (`vitest run` для CI, `vitest` для watch).
Конфиг — `apps/web/vitest.config.ts` (jsdom env, setup-файл для
`@testing-library/jest-dom`, alias `@/*` зеркалирует `tsconfig`).

`react-d3-tree` сам по себе **не тестируется** в jsdom — он завязан
на canvas / ResizeObserver / d3-zoom, которые в jsdom не работают.
Покрываем только **pure helpers** (`toRawNode` и аналогичные), которые
не зависят от DOM. Когда дойдём до e2e (Phase 4.x), интеграционные сценарии
с самой визуализацией покроет Playwright (Вариант D становится дополнением,
не заменой).

Playwright откладываем до отдельного ADR в Phase 4.x, когда появится auth
flow и импорт UI — там реальный браузер уже окупается.

## Последствия

**Положительные:**

- Каждая новая страница в Phase 4.4+ имеет «куда положить тест»
  без рефакторинга инфраструктуры.
- `pnpm -F @autotreegen/web test` — единая команда; легко включить
  в `scripts/check.{ps1,sh}` и CI-job (отдельным шагом, см. «Что нужно
  сделать»).
- Выбор Vitest совпадает с трендом экосистемы (Vue, SvelteKit, и сама
  Vite-команда). При появлении контрибьюторов порог входа низкий.

**Отрицательные / стоимость:**

- Дополнительный набор devDependencies (`vitest`, `@vitejs/plugin-react`,
  RTL-семейство, `jsdom`) — ~25 МБ в `node_modules`.
- `vitest.config.ts` — ещё один конфиг рядом с `next.config.ts` и
  `tsconfig.json`. Алиасы дублируются с tsconfig (минимальный дубль:
  одна строка `"@" → "./src"`).

**Риски:**

- Vitest может разойтись с Jest API в редких краях (snapshot форматы,
  некоторые matcher'ы). Mitigation: используем `expect` из `vitest`
  напрямую, не из `@jest/globals`.
- Если потом захочется Playwright Component Tests, придётся держать
  оба runner'а параллельно. Mitigation: разные `include` глобы — Vitest
  на `*.test.ts`, Playwright на `*.e2e.ts`.

**Что нужно сделать в коде:**

1. `apps/web/package.json` — добавлены scripts `test`, `test:watch`,
   `test:ui` и devDeps (`vitest`, `@vitejs/plugin-react`, `jsdom`,
   RTL-семейство).
2. `apps/web/vitest.config.ts` — jsdom env, setup-файл, alias `@/*`.
3. `apps/web/src/test/setup.ts` — импорт `@testing-library/jest-dom`.
4. `apps/web/src/components/__tests__/pedigree-tree-helpers.test.ts` —
   первые тесты на `toRawNode`.
5. `pedigree-tree.tsx` — `toRawNode` экспортирован для тестируемости.
6. Phase 4.4+ — добавить запуск `pnpm -F @autotreegen/web test`
   в CI workflow и `scripts/check.{ps1,sh}` отдельным PR (вне scope этого
   ADR — здесь только инфра).

> **TODO (после merge PR #96):** ~5-минутная ручная правка — добавить
> `pnpm -F @autotreegen/web test` в `scripts/check.ps1` (и `check.sh` для
> парности, проверяемой `tests/test_ci_parity.py`) и в job `lint-and-test`
> в `.github/workflows/ci.yml`. После этого frontend-тесты становятся
> обязательным гейтом перед `git push`, как и backend pytest.

## Когда пересмотреть

- Если появится auth flow / mutations (Phase 4.x) и unit-тестов на
  компоненты станет слишком много для удобной отладки в jsdom —
  ввести Playwright Component Tests как complement.
- Если Vitest разойдётся с RTL по совместимости (например, RTL начнёт
  требовать что-то Jest-специфичное) — пересмотреть выбор.
- При переходе на serverComponents-only архитектуру — оценить, не нужны
  ли сервер-сайд тесты с Next встроенным runner'ом.

## Ссылки

- ADR-0001 — tech-stack (Next.js 15 / TS strict / pnpm).
- ADR-0010 — `apps/web/` first slice.
- ADR-0013 — tree visualization (Phase 4.3, источник `toRawNode`).
- Vitest docs: <https://vitest.dev/>.
- React Testing Library: <https://testing-library.com/docs/react-testing-library/intro/>.
