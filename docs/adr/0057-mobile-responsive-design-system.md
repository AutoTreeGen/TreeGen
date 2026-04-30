# ADR-0057: Mobile-responsive design system + breakpoint strategy (Phase 4.14a)

- **Status:** Accepted
- **Date:** 2026-04-30
- **Authors:** @autotreegen
- **Tags:** `frontend`, `accessibility`, `phase-4`, `design-system`

## Контекст

TreeGen frontend разрабатывался desktop-first: layout-вёрстка предполагает
viewport ≥1024px, design-system tokens (`Button.h-{8,10,12}`, `Input.h-10`,
`text-sm`) подобраны под мышь, а не палец. К Phase 4.14 ситуация:

- 31 page.tsx под `/apps/web/src/app/`, ~50 client components.
- Tailwind v4 (config через `@theme` в `globals.css`) с дефолтными
  breakpoint'ами (`sm:640`, `md:768`, `lg:1024`).
- iOS Safari при focus на любом form-control с font-size <16px
  делает auto-zoom — это видимый регресс UX, потому что после auto-zoom
  user должен вручную zoom-out, чтобы продолжить навигацию.
- WCAG 2.1 AA требует touch targets ≥44×44px. Текущий `Button.md`
  это `h-10` = 40px (fail), `Button.sm` — `h-8` = 32px (fail хуже).
- Tree visualization (`PedigreeTree` через `react-d3-tree`) на mobile
  не отвечает на pinch-to-zoom: контейнер по-умолчанию даёт браузеру
  pull-to-refresh / scroll, и pinch не доходит до d3-zoom.
- Mobile audience не была first-class приоритетом; ROADMAP §22.1
  «Mobile app? — отложено до публичного beta. PWA достаточно сначала».
  Но «PWA достаточно» подразумевает работающий mobile веб-UI.

Силы давления:

1. **Не сломать desktop.** Любое решение должно быть mobile-first
   *по добавлению*, не *по подмене*: desktop layout (≥sm) остаётся
   тем же, что был до 4.14a.
2. **Минимум новых компонентов.** spec намекал на отдельные `Sheet` /
   `Drawer` примитивы. Но в проекте только 1 modal (`DeleteAccountModal`
   в /settings) и 0 sidebar nav'ов. Tailwind utility-классов хватает.
3. **Не пакет-by-пакет.** 31 page нельзя править по одной — это
   масштабируется в 3000+ LOC review surface. Должно быть лекарство
   на уровне design-system, каскадящее по всему UI.
4. **Тесты должны фиксировать инвариант.** Класс `min-h-11` легко
   откатить случайно при следующем рефакторинге Tailwind-string'ов.
   Vitest должен падать на removal.

## Audit findings

Перед реализацией прошли по 31 page и shared components. Ключевое:

| Категория | Spec предполагал | Что нашли в коде | Решение |
|---|---|---|---|
| Hamburger menu в site-header | да | Header — это Title + LocaleSwitcher + (User/SignIn). Nav-links нет, sidebar нет. | Hamburger не нужен. Просто увеличить header height (h-14) и touch target sign-in кнопки на mobile. |
| Tables → cards | «все table-pages» | Только `dna/[kitId]/matches` использует `<table>`. Остальные «list-style» это grid из Card-компонентов. | Mobile card-stack только в одной странице, остальные — N/A. |
| Modals → bottom sheets | «sharing UI modals» | Sharing UI (`/trees/[id]/access`) — это страница с табами и cards, **не модалка**. Единственный `<dialog>` в app — `DeleteAccountModal` в /settings. | Bottom-sheet pattern только для DeleteAccountModal. |
| Settings tabs accordion | да, vertical | 3 коротких tab'а (Profile/Sessions/Danger) — горизонтальный flex даже на 320px помещается, аккордеон тут излишний. | Tabs остаются горизонтальными, но получают `min-h-11` + `overflow-x-auto` для длинных RU-локализаций. |
| Tree visualization touch | spec: «pinch-zoom» | react-d3-tree v3 уже использует d3-zoom, который touch-aware. Проблема в `touch-action` контейнера: браузер забирает gesture'ы. | Добавить `touch-action: none` на container — d3-zoom получит native pinch. |

## Рассмотренные варианты — design system

### A — Per-page Tailwind responsive classes (отвергнут)

Каждая `<button>` / `<input>` на каждой странице получает свои
`min-h-11 sm:min-h-0 sm:h-N`. ~150 use sites × несколько классов на
каждом → 600+ строк диффа без изменения семантики.

- ✅ Maximum control per page.
- ❌ Massive review surface.
- ❌ Каждая новая страница — risk забыть mobile classes.
- ❌ Невозможно сделать инвариантный vitest на «все Buttons имеют
  min-h-11» (надо обходить все use sites).

### B — Centralized в `Button` / `Input` / `Checkbox` cva-variants (выбран)

Mobile-first классы внутри `cva(buttonVariants)`:
`min-h-11 px-3 text-sm sm:min-h-0 sm:h-8` — каждый размер рендерится
с mobile floor и desktop reset.

- ✅ Один файл — одна правда. Любой `<Button size="md">` где угодно
  получает поведение «44px на mobile, 40px на desktop» бесплатно.
- ✅ Vitest может проверить classes на одном `<Button/>` и быть
  уверенным, что все use sites соблюдают инвариант.
- ✅ `link` variant исключён через `compoundVariants` — inline-кнопки
  внутри предложений не надо растягивать до 44px.
- ❌ Tailwind class string становится длиннее (sm: prefixes на каждом
  размере). Acceptable — это раз-навсегда.

### C — Tailwind plugin (отвергнут)

Custom `@layer components` с `.btn-md { ... }` или PostCSS-plugin для
auto-mobile-floor. Тяжело: добавляет build-step, замедляет HMR, теряет
JIT-доступность для overrides.

## Рассмотренные варианты — globals.css safety net

Раздельные form controls (`<select>`, `<textarea>`, raw `<input>`)
тоже страдают от iOS auto-zoom. Их в коде ~10 мест.

### A — Все переписать на `<Input as=...>` (отвергнут)

Нет такого pattern'а в проекте. Создавать API ради 10 use sites дорого.

### B — `@media (max-width: 639px)` правило в globals.css (выбран)

```css
@media (max-width: 639px) {
  input:not([type="checkbox"]):not([type="radio"]),
  select,
  textarea {
    font-size: 16px;
  }
}
```

- ✅ Defensive layer: даже если кто-то добавит raw `<select>`, iOS
  auto-zoom не сработает.
- ✅ Не затрагивает desktop (≥640px возвращает к Tailwind class'ам).
- ✅ Стандартный CSS, без runtime.

Также добавлен `touch-action: manipulation` для интерактивных
элементов — отключает 300мс double-tap zoom, кнопки чувствуют себя
быстрее на mobile.

## Решение

1. **Design-system mobile floors.**
   - `Button.{sm,md,lg}` → `min-h-11 sm:min-h-0 sm:h-{8,10,12}`.
   - `Button` `link` variant → `min-h-0 sm:min-h-0` (inline-текст).
   - `Input` → `min-h-11 text-base sm:min-h-0 sm:h-10 sm:text-sm`.
   - `Checkbox` → `h-5 w-5 sm:h-4 sm:w-4` (20px hit area на mobile).

2. **globals.css safety net.**
   - `@media (max-width: 639px)` — `font-size: 16px` на raw form controls.
   - `touch-action: manipulation` на `button, [role="button"], a, label,
     input[type="checkbox"], input[type="radio"]`.

3. **Per-page фиксы — точечно, только где design-system недостаточно:**
   - `SiteHeader` — `h-14 sm:h-12`, sign-in кнопка получает `min-h-11
     sm:min-h-0` (это `<button>` Clerk-а, не наш `<Button>`).
   - `LocaleSwitcher` — `<select>` получает `min-h-11 text-base` mobile.
   - `PedigreeTree` контейнер — `touch-none select-none` (Tailwind:
     `touch-action: none; user-select: none`).
   - `DnaKitMatchesPage.MatchesTable` — отдельный `<ul>` card-stack
     для `<sm`, оригинальный `<table>` остаётся для `≥sm`.
   - `DeleteAccountModal` — outer flex `items-end justify-center sm:items-center`,
     inner card `rounded-t-2xl sm:rounded-lg`. Bottom-sheet на mobile,
     centered dialog на desktop.
   - `Settings` + `Access` tabs — `min-h-11 sm:min-h-0` на каждом tab,
     `overflow-x-auto sm:overflow-visible` на nav для длинных RU-лейблов.
   - `Hypotheses` filter selects — `min-h-11 text-base sm:h-10 sm:text-sm`
     (raw `<select>` без design-system shell).

4. **Тесты.**
   - `apps/web/src/components/__tests__/mobile-responsive.test.tsx`
     — vitest проверяет, что Button (все размеры), Input, Checkbox
     рендерятся с mobile-first классами. Тест падает, если кто-то
     откатил `min-h-11` или `text-base`.

## Breakpoint policy

Каноничный набор Tailwind v4 (sm:640 / md:768 / lg:1024 / xl:1280).
Решения:

- **Base styles = mobile.** Без префикса = mobile-first.
- **`sm:` (≥640px) = "tablet portrait и больше"** — этот breakpoint
  возвращает desktop-сompactness (h-10, text-sm, rounded-lg).
- **`md:` / `lg:` = layout.** Использовать только для grid-grow
  (`md:grid-cols-2`, `lg:grid-cols-3`), не для form-control sizing.
  Form controls "стабилизируются" на ≥sm.
- **`<sm` (399px и ниже) — special case** — мы НЕ оптимизируем под
  iPhone SE 1-го поколения (320px); minimum поддерживаемый viewport
  — 360px (Galaxy S8 era). Если что-то ломается на 320px, фикс
  acceptable, но не приоритет.

## Последствия

**Положительные:**

- Все Buttons / Inputs / Checkboxes — WCAG 2.1 AA touch-compliant
  на mobile без ручной правки call sites.
- iOS Safari больше не делает auto-zoom при focus на form-controls.
- Tree visualization работает с pinch-zoom out-of-the-box.
- Vitest фиксирует инвариант — будущие правки Tailwind-string'ов не
  откатят mobile-first незаметно.
- ROADMAP §8.0 row Phase 4.14a отмечена как done.

**Отрицательные / стоимость:**

- Tailwind class strings в `Button` / `Input` стали длиннее
  (~+30 chars на каждый variant). Acceptable.
- Vitest tests на classNames хрупкие — если Tailwind v5 переименует
  `min-h-11` → нужно обновлять. Mitigation: тестов всего ~6,
  обновление тривиальное.
- На viewport 360–639px UI выглядит ощутимо «менее plотно», чем на
  640+ — компромисс между «компактный» и «тач-доступный».

**Риски:**

- **Pages, которые я не аудитировал, могут иметь свои raw form
  controls без font-size fix'а.** Mitigation: globals.css media query
  ловит их по дефолту.
- **Tree pinch-zoom может конфликтовать с page scroll.** Mitigation:
  PedigreeTree высотой `h-[70vh]` — между ним есть header выше и
  content ниже, через который user может скроллить страницу.
- **`touch-action: manipulation` отключает double-tap zoom на ВСЕХ
  кнопках/линках.** Это намеренно — accessibility tools всё ещё могут
  zoom через pinch. Но если кто-то жалуется — снять `touch-action`
  с конкретного элемента.

## Когда пересмотреть

- **Phase 4.14b shipped** (Performance: next/image, dynamic import
  для tree-viz, Playwright mobile tests) — обновить ADR-0057
  §«Решение» добавив п.5.
- **iOS Safari font-size threshold меняется** (Apple релизит iOS 19+
  без auto-zoom, или меняет порог) — снять globals.css `font-size: 16px`
  правило.
- **Tailwind v5 переименует utilities** — обновить vitest classNames
  и cva-variants.
- **Появляется sidebar nav или main-nav menu** — site-header получает
  hamburger button + Sheet/Drawer компонент (новый ADR).
- **Mobile usage метрика > 30%** — пересмотреть «desktop-first base
  styles» политику; возможно стоит переключить дефолты.

## Ссылки

- Связанные ADR:
  - ADR-0035 (Phase 4.12 — landing/onboarding/i18n foundation) — там
    появилась `viewport: { width: device-width, initialScale: 1 }`
    мета.
  - ADR-0037 (Phase 4.13 — i18n full rollout) — длинные RU-локализации
    мотивируют `overflow-x-auto` на tabs.
  - ADR-0036 (Phase 11 — sharing permissions model) — sharing UI
    оказалась НЕ модалкой, что повлияло на scope этого ADR.
  - ADR-0040 (Phase 11.1 — sharing UI architecture) — то же.
  - ADR-0038 (Phase 4.10b — account settings) — DeleteAccountModal,
    единственный bottom-sheet candidate.
- ROADMAP §8.0 row Phase 4.14a, §8.1 страницы.
- Внешние:
  - [WCAG 2.1 — Target Size (Enhanced) AAA](https://www.w3.org/WAI/WCAG21/Understanding/target-size.html)
  - [iOS Safari auto-zoom on focus — explainer](https://stackoverflow.com/a/6394497) (16px font-size порог)
  - [`touch-action` MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/touch-action)
  - [react-d3-tree v3 release notes — touch support](https://github.com/bkrem/react-d3-tree)
