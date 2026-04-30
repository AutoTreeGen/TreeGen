# ADR-0061: Onboarding tour design (Phase 4.15)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `frontend`, `onboarding`, `i18n`, `phase-4`
- **Supersedes:** —
- **Related:** [ADR-0033](./0033-authentication-via-clerk.md) (Clerk auth),
  [ADR-0035](./0035-landing-and-marketing-strategy.md) (landing + signup
  flow), [ADR-0038](./0038-account-settings-architecture.md) (settings
  contract), [ADR-0052](./0052-i18n-full-coverage.md) (i18n rollout).

## Контекст

Phase 4.12 (#117) выкатил landing + sign-up. Phase 4.13 (#141)
доделал i18n для authenticated app. После регистрации новый user
оказывается на `/dashboard` со 0 деревьев и тут же редиректится в
`/onboarding` (3-step wizard: source → import → done). Wizard
доводит до первого дерева, но НЕ объясняет, что ещё умеет приложение:

- где поиск персон,
- как смотреть pedigree visualization,
- что такое DNA matches,
- где гипотезы и почему их нужно review'ить,
- как поделиться с family.

Без tour'а user либо натыкается случайно, либо не находит и
отваливается. Phase 4.15 закрывает эту дыру: интерактивный tour
по 7 ключевым местам приложения, запускается один раз после первого
sign-up'а, опционально перезапускается из settings.

## Силы давления

- **Bundle weight.** Web уже грузит React 19 + Next 15 + Clerk + react-
  query + next-intl + Radix-slot. Добавлять ещё library только ради
  tour'а — дорого; tour появляется один раз в жизни user'а.
- **React 19 compat.** Известные tour-libraries (react-joyride, intro.js
  React-wrapper'ы) исторически отстают по React-major'ам — joyride
  на момент написания ADR ещё не имеет stable React-19 release'а.
- **CLAUDE.md §3.5 (Privacy by design).** Persistence flag'и tour'а —
  user-preference, не sensitive PII; хранение в Clerk metadata
  acceptable. Backend-row для tour state — overkill (overhead миграции
  ради 3 boolean'ов).
- **i18n parity (ADR-0052).** EN + RU обязательно.
- **CLAUDE.md §6 (Тесты > 80%).** Custom UI = больше кода = больше
  тестов; OK trade-off, потому что behaviour детерминирован.

## Рассмотренные варианты

### Вариант A — Кастомный tour без external library

Tour-overlay = простой modal-card в центре viewport'а с back / next /
skip / close. Опциональный highlight-якоря по `data-tour-id` через
`document.querySelector` + `scrollIntoView` + ring-overlay.

- ✅ Bundle: 0 KB добавки сверх существующих primitive'ов (Button,
  Card, Tailwind).
- ✅ React 19 ready by definition (это наш код).
- ✅ Стилизация уже совпадает с design-system (`color-accent`,
  `color-surface`, `color-ink-*`).
- ✅ Тестируется тривиально: testing-library + vi.mock на
  `useUser`, `usePathname`. Нет hidden side-effects (portal'ы,
  injected styles).
- ❌ Нужно самому реализовать step-state, persistence-hook'и, anchor-
  highlight. Реализация ~280 LOC включая RestartTourButton.
- ❌ Нет «spotlight» (затемнение всего кроме якоря) — вместо этого
  обычный backdrop-overlay. Мы решили, что spotlight — nice-to-have,
  а не must-have для 7 шагов: половина шагов — текстовые без anchor'а
  (welcome, finish), spotlight там бесполезен.

### Вариант B — `react-joyride`

Самая популярная tour-library для React, mature API.

- ✅ Spotlight, beacon-mode, popper-positioning «из коробки».
- ✅ Сложная навигация (skip-jump-by-target, scroll-control) уже
  реализована.
- ❌ ~50 KB minified gzipped. Загружается даже после того, как tour
  пройден (если не lazy-import-ить через dynamic с `ssr: false` —
  что добавляет boilerplate).
- ❌ React 19 поддержка на дату ADR'а помечена как «experimental»;
  есть открытые issue'ы про runtime-warnings.
- ❌ Стилизация через `styles` prop с inline-объектами; не интегрируется
  с нашими Tailwind-tokens без обёртки. Получается двойной труд.
- ❌ Тестирование через testing-library требует дополнительных setup'ов
  (jest-dom selectors, scrollIntoView mock); не критично, но плюс
  один источник flakiness.

### Вариант C — `intro.js` или `shepherd.js`

- ❌ Не React-native (intro.js — DOM-API library, shepherd — vanilla);
  нужны wrapper'ы либо ad-hoc useEffect-bridge'и. Обоих минусов варианта B
  без его плюсов.

### Вариант D — Skip tour'а вообще

Положиться на «empty-state + tooltips per page» как permanent UI.

- ❌ Phase 4.13b показал, что без guided-flow user не находит
  pedigree-tab из persons-page (telemetry: 0 кликов после первого
  import'а в demo-account'ах).
- ❌ Оставляет critical gap между sign-up и первым «aha»-моментом.

### Решение

Выбран **Вариант A — кастомный tour**. ~280 LOC ноль-деп stays под
control'ом, React-19-safe, легко тестируется, не наказывает bundle.
Spotlight отсутствует осознанно — для 7 шагов с половиной текстовых
modal-overlay достаточен.

## Persistence model

Tour state хранится в **Clerk `unsafeMetadata.tour`** — JSON-объект:

```json
{
  "tour_completed": true,
  "tour_skipped": false,
  "tour_completed_at": "2026-04-30T12:34:56.000Z"
}
```

Почему Clerk metadata, а не наш backend:

1. **Совпадает с паттерном locale dual-write (ADR-0038).** Locale
   user'а уже хранится в Clerk `unsafeMetadata` — single source of
   truth для frontend-only preference'ов.
2. **Нет API-сurface'а.** Tour state нужен только в браузере; backend
   о нём не знает и знать не должен. Не делаем endpoint'ы, ORM-
   модель, миграцию ради 3 boolean'ов.
3. **Survives sign-out.** Metadata — часть Clerk-account'а, не
   browser-storage'а; user'у не покажет tour снова при логине с
   нового устройства, если он уже его прошёл.
4. **Не PII.** `unsafeMetadata` не годится для PII (Clerk warning),
   но 3 булева тура — публичная preference, не sensitive.

State semantics:

- `tour_completed=true` — user прошёл все 7 шагов и нажал Finish.
  Auto-trigger никогда не сработает.
- `tour_skipped=true` — user нажал Skip; tour дальше не показывается
  до явного restart'а.
- Оба `false` (или `undefined`) — auto-trigger при первом заходе
  на `/dashboard`.

Close-button НЕ персистит (tour может повторно открыться при следующем
визите). Это «remind me later», в отличие от skip («больше не
показывать»).

## Auto-trigger логика

```text
if (Clerk.isLoaded && user) {
  if (?restartTour=1 в URL) → открыть tour (manual)
  else if (pathname === "/dashboard"
           && !tour_completed
           && !tour_skipped
           && не открывали в этой сессии) → открыть tour (auto)
}
```

«Не открывали в этой сессии» (`sessionDismissedRef`) важен: иначе
после Close или Skip → setOpen(false) → useEffect снова проверяет
условия → если не успели persist'нуться (race), снова откроется.

## Mount point

Спецификация Phase 4.15 запросила mount в
`apps/web/src/app/(authenticated)/layout.tsx`. На дату Phase 4.15 такого
файла НЕ существует:

- `(authenticated)/` route-group содержит ТОЛЬКО `settings/`. Все
  остальные authenticated маршруты (`/dashboard`, `/persons`, `/trees`,
  `/hypotheses`, `/dna`, `/onboarding`) — top-level.
- Полный rerouting под `(authenticated)/` — out of scope для Phase 4.15
  и потенциально breaks redirect'ы middleware'а Clerk'а.

**Решение:** mount `<OnboardingTour />` в **root `app/layout.tsx`**
внутри `<SignedIn>` Clerk-обёртки. Это ровно эквивалентно намерению
спеки (показывать tour только authenticated user'ам), без затрат на
route-group миграцию. Компонент сам внутри проверяет `pathname` и
открывается auto только на `/dashboard`.

## Restart tour

Кнопка `Restart tour` живёт в `/settings` (Profile-таб). Нажатие:

1. Записывает в Clerk metadata `{tour_completed: false, tour_skipped: false}`.
2. Редиректит на `/dashboard?restartTour=1`.
3. На dashboard'е компонент видит query-flag и откроется даже если
   metadata ещё не успела propagate'нуться.

Спека Phase 4.15 указала путь
`apps/web/src/app/(authenticated)/settings/account/page.tsx`. Такого
sub-route'а тоже не существует — settings — это single page с tab'ами
(см. ADR-0038). Кнопка добавлена в существующий `settings/page.tsx`,
видна на Profile-табе как отдельная карточка под формой профиля. Не
делаем отдельный tab «Help» ради одной кнопки — tour это preference,
не feature-area.

## i18n

Все tour-strings — под `onboarding.tour.*` namespace в
`messages/en.json` + `messages/ru.json`. Parity check —
`tests/test_i18n_parity.py` (ADR-0052) подхватит новые ключи
автоматически.

## Что НЕ делаем в Phase 4.15

- Spotlight (затемнение всего кроме якоря). Modal-overlay достаточен.
- Telemetry tour-completion-rate. Phase будет позже после Phase 8 (analytics).
- A/B тесты разных tour-flow'ов. Single canonical version пока.
- Tour для existing-user'ов (миграция). Их `unsafeMetadata.tour`
  пустой → они увидят tour при следующем заходе на `/dashboard`. Это
  ожидаемое поведение, а не ошибка.
- Tour-step deep-linking (`/dashboard?tourStep=3`). YAGNI; restart с
  beginning'а — достаточно.

## Migration & backwards compatibility

- НЕТ backend changes.
- НЕТ alembic migrations.
- НЕТ breaking changes контракта Clerk metadata: `unsafeMetadata.locale`
  (ADR-0038) и `unsafeMetadata.tour` (этот ADR) — independent под-keys.

## Ссылки

- [ADR-0033](./0033-authentication-via-clerk.md) — Clerk auth + JIT
  user creation; tour-trigger полагается на `useUser().isLoaded`.
- [ADR-0035](./0035-landing-and-marketing-strategy.md) — sign-up flow,
  /onboarding wizard; tour запускается ПОСЛЕ wizard'а.
- [ADR-0038](./0038-account-settings-architecture.md) — settings page;
  RestartTourButton добавлен в Profile-таб.
- [ADR-0052](./0052-i18n-full-coverage.md) — i18n parity policy.
- [Clerk unsafeMetadata docs](https://clerk.com/docs/users/metadata#unsafe-metadata).
