# ADR-0037: i18n full-rollout, error-code messages, lint enforcement (Phase 4.13)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `frontend`, `i18n`, `dx`, `phase-4`

## Контекст

Phase 4.12 (ADR-0035) поставил cookie-based i18n foundation на лендинге
и onboarding'е. Внутри приложения — после sign-in — все строки всё
ещё английские, а это главная аудитория проекта (русско-говорящие
исследователи Восточной Европы). Phase 4.13 расширяет i18n на
authenticated routes и фиксирует:

- куда складывать ошибки API (общий компонент `<ErrorMessage code=...>`),
- как enforce'ить «никакого raw-English-JSX в auth-страницах»,
- куда переехал LocaleSwitcher.

Силы давления:

1. **Размер scope.** ~5000 LOC через 19 authenticated pages — реальная
   полная экстракция строк это 1500+ строк диффа. Должно быть
   разделено на 4.13a (foundation + smallest pages) и 4.13b (большие
   pages).
2. **Эстетический gap локалей.** Если в одном экране 80% строк русские
   и 20% случайно остались английские — это хуже, чем 100% en. Lint-rule
   нужен, чтобы это не происходило незаметно.
3. **Ошибки API.** Фрагментированы по pages: `<p>Failed to load
   preferences.</p>`, `<p>Couldn't load notifications.</p>`. Без общего
   компонента — N локализуемых строк × M страниц.
4. **biome без i18n-плагина.** Нет ESLint в проекте → готовый плагин
   типа `eslint-plugin-i18next-strict` не применим.
5. **next-intl уже стоит.** Не вводим параллельную систему — 4.13
   это просто продолжение 4.12.

## Рассмотренные варианты

### Lint enforcement

#### A — ESLint + плагин i18next-strict (отвергнут)

- ✅ Готовый AST-based анализ JSX text nodes.
- ❌ Ставим ESLint целиком ради одного rule'а. Конфликт с biome
  (двойная lint-run в pre-commit + CI).

#### B — TypeScript-плагин с custom transformer (отвергнут)

- ✅ Type-safe.
- ❌ Heavy: добавляет TS plugin lifecycle, замедляет dev-build.

#### C — Custom Python-regex hook в pre-commit (выбран)

`scripts/check_i18n_strings.py` — простой regex по `.tsx` файлам в
allowlist'е (`apps/web/src/app/{dashboard,persons,dna,sources,hypotheses,
familysearch,settings,trees}/**.tsx` + конкретные shared components).

- ✅ Zero install — Python-3 уже есть для ruff/mypy/markdownlint.
- ✅ Прицельно: не трогает marketing pages (они уже сделаны и работают),
  не трогает demo / onboarding / pricing.
- ✅ Allowlist доменных терминов (GEDCOM, DNA, FamilySearch, ...) для
  фолс-позитивов.
- ❌ Regex-based: ловит `<h1>Some text</h1>`, пропускает динамические
  выражения, шаблонные литералы, JSX-аттрибуты `placeholder="..."`.
- 🟡 Mitigation: vitest `locale-rendering.test.tsx` ловит missing-key
  fallback'и в обоих локалях — двойная защита.

### Error-code messages

#### Vendor pattern A — каждая страница пишет свою строку (status quo)

`<p>Failed to load X.</p>` — повторяется N раз, локализуется N раз.
Без общей конвенции легко спутать «ошибка загрузки» и «ошибка
сохранения».

#### Vendor pattern B — `<ErrorMessage code={...}/>` + `errors.*` namespace (выбран)

```tsx
{isError ? <ErrorMessage code="preferencesLoadFailed" onRetry={refetch} /> : null}
```

- ✅ Один компонент, один retry-button-pattern, один visual.
- ✅ Доступен `role="alert"` бесплатно.
- ✅ Доменные коды (preferencesLoadFailed, notificationsLoadFailed,
  treesLoadFailed) живут в `errors.*` рядом с generic'ами (network,
  unauthorized, forbidden, notFound, validation, rateLimit).
- ✅ Когда придёт unification API errors (Phase 4.x), достаточно
  отмаппить server error code → `ErrorCode` enum.
- ❌ Каждый новый код требует синхронного добавления в обе messages.
  Mitigation: `messages parity` тест в `locale-rendering.test.tsx`
  — `en.json keys ⊆ ru.json keys` и наоборот.

### LocaleSwitcher placement

LocaleSwitcher был в hero лендинга. После 4.13 он также висит в
`SiteHeader`, чтобы залогиненный user мог переключить язык не
выходя на лендинг. На лендинге — оставлен дубль (вторая копия
рендерится в hero), потому что landing's hero — visible above-the-fold
для unsigned visitor'а, а site-header может быть скрыт sticky-overlap'ом.

## Решение

1. **Lint enforcement:** custom Python regex hook
   (`scripts/check_i18n_strings.py`), зарегистрирован в pre-commit
   как `check-i18n-strings` с `repo: local`. Allowlist доменных
   терминов и shared-components. Скоп: только authenticated routes.

2. **Error-code messages:** `<ErrorMessage code={ErrorCode}/>` в
   `apps/web/src/components/error-message.tsx`. Namespace `errors.*`
   в `messages/{en,ru}.json` с базовым набором (`generic`, `network`,
   `unauthorized`, `forbidden`, `notFound`, `validation`, `rateLimit`)
   плюс доменные (`preferencesLoadFailed`, `notificationsLoadFailed`,
   `treesLoadFailed`). Каждая страница, которая раньше писала
   `<p>Failed to load X.</p>`, теперь рисует `<ErrorMessage code=…/>`.

3. **LocaleSwitcher:** перенесён в `SiteHeader`, который рендерится
   на ВСЕХ страницах (auth + public). На landing'е остаётся
   дополнительная копия в hero — она задумана как call-to-action
   для unsigned visitor'а до того, как они докрутили до header'а.

4. **Phase 4.13 split:**
   - **4.13a (этот PR):** infrastructure + smallest authenticated pages.
     Конвертированы: `dashboard`, `settings/notifications`. Shared:
     `site-header`, `notification-bell`. Новое: `<ErrorMessage>`,
     `<LocaleSwitcher>` в header, lint hook, locale-rendering vitest,
     ADR-0037.
   - **4.13b (follow-up):** trees/[id]/*(4 pages, ~1300 LOC),
     persons/[id]/* (3 pages, ~1000 LOC), familysearch/*(3 pages,
     ~600 LOC), dna/* (3 pages, ~700 LOC), hypotheses/[id], sources/[id].
     Каждая фаза 4.13b может смержиться отдельно, lint-hook начнёт
     ловить regression'ы по мере того, как новые pages входят в его
     allowlist.

## Последствия

**Положительные:**

- Russian user не видит mixed-locale UI на dashboard / settings.
- Любая новая ошибка API теперь автоматически локализована (один
  компонент, один namespace).
- LocaleSwitcher доступен везде — UX-fix, который Phase 4.12 не
  закрыл.
- Lint hook предотвратит regression'ы на authenticated pages.
- Messages-parity тест ловит расхождения между `en.json` и `ru.json`.

**Отрицательные / стоимость:**

- Custom regex hook не покрывает JSX-атрибуты (`placeholder`, `aria-label`,
  `title`). Mitigation: vitest locale-rendering test + дисциплина
  ревью.
- Каждый новый error-code требует двух правок (en + ru). Mitigation:
  parity тест.
- Phase 4.13 разбит на 4.13a/4.13b — 4.13b добавит ~1000+ строк дифф,
  потребует отдельного review cycle.

**Риски:**

- **Hook ложноположительные.** Если allowlist не покрывает доменный
  термин — devs будут раздражены. Mitigation: `_DOMAIN_TERMS_ALLOW`
  легко расширяется в одном месте.
- **Hook ложноотрицательные.** JSX вида ``<p>{condition ? "Yes" :
  "No"}</p>`` пропускается. Mitigation: vitest `messages parity` +
  дисциплина в ревью + Phase 4.14 переход на ESLint AST если
  ложноотрицательные станут массовыми.
- **Phase 4.13b откладывает большие pages.** Russian user продолжает
  видеть mixed-locale на trees / persons / familysearch / dna /
  hypotheses / sources до Phase 4.13b merge. Acceptable: эти pages
  доступны только после login + tree-import; для unsigned visitor'а
  и first-time онбординга — landing уже на ru.

## Когда пересмотреть

- **Phase 4.13b shipped** → обновляем ADR-0037 §«Phase 4.13 split»
  как «полностью закрыт».
- **Hook ложноположительные превышают N/неделю** → расширяем
  `_DOMAIN_TERMS_ALLOW` или переходим на AST-based lint в Phase 4.14.
- **JSX-аттрибуты раз на раз ловят raw EN на ревью** → расширяем
  hook на `placeholder=`, `aria-label=`, `title=`.
- **Появляется 3-я локаль** (he? uk?) → перепроверяем messages parity
  тест (он сейчас 2-сторонний, на N сторон надо обобщать).
- **next-intl выкатывает плагин для biome** → переходим на canonical.

## Ссылки

- Связанные ADR:
  - ADR-0035 (Phase 4.12 — landing/onboarding/i18n foundation) — то,
    что 4.13 расширяет.
  - ADR-0008 (CI / pre-commit parity) — почему hook идёт через
    `repo: local` (не отдельный repo).
  - ADR-0025 (frontend test runner — vitest) — runner, на котором
    locale-rendering тесты работают.
- ROADMAP §8 (Phase 4 — Веб-сайт MVP), §8.0 row Phase 4.13.
- Внешние:
  - [next-intl docs — Server / Client Components](https://next-intl-docs.vercel.app/docs/usage/configuration)
  - [pre-commit local hooks](https://pre-commit.com/#repository-local-hooks)
