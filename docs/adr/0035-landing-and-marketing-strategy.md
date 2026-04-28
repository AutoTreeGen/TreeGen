# ADR-0035: Landing page, onboarding flow, and i18n foundation (Phase 4.12)

- **Status:** Accepted
- **Date:** 2026-04-28
- **Authors:** @autotreegen
- **Tags:** `frontend`, `marketing`, `i18n`, `onboarding`, `seo`, `phase-4`

## Контекст

До Phase 4.12 у `/` был placeholder («coming soon»), `/sign-up` ещё не
подключён (ждёт Phase 4.10 Clerk auth), а у нового user'а нет
проводника от посадки до первого импорта дерева. Phase 4.12 закрывает
эту дыру: production-grade marketing landing + 3-шаговый onboarding
wizard + i18n foundation (en + ru) + lead-capture + SEO-минимум.

Силы давления:

1. **Public-facing first impression.** Landing — единственный шанс
   объяснить за 5 секунд: «evidence-based, hypothesis-aware, DNA +
   GEDCOM + FamilySearch, домен — Восточная Европа». Без него все
   downstream-фазы (auth, биллинг, marketing-кампании) — слепые.
2. **Domain niche → russian audience.** Главная аудитория проекта —
   русско- и иврито-язычные исследователи jewish/Eastern European
   genealogy. EN-only landing — упущенная конверсия. Поэтому ru-локаль
   — first-class, не «потом добавим».
3. **Auth не готов.** Phase 4.10 (Clerk) идёт параллельно. Landing
   должен:
   - работать БЕЗ login (publicly indexable);
   - указывать CTA на `/sign-up` (Clerk подменит при merge'е).
4. **SEO.** Маркетинговые роуты должны индексироваться, auth-protected
   — НЕТ, даже если редиректят на login. Sitemap/robots обязаны
   вырезать второе.
5. **Lead capture без auth.** Юзер хочет «когда выйдет — напишите»;
   без создания аккаунта. Нужен POST /waitlist с минимальной anti-abuse.
6. **Perf budget.** Landing с тяжёлыми скриншотами / видео ломает LCP.
   Phase 4.12 фиксирует Lighthouse perf ≥ 90 как gate; placeholder
   inline-SVG mock'и вместо растровых скриншотов до launch'а.

## Рассмотренные варианты

### A — Только landing (без onboarding, без i18n) (отвергнут)

Минимальный «MVP marketing». Plus: меньше LOC. Минусы:
конверсия упадёт без проводника новичка («что мне делать дальше?»),
и придётся переделывать landing когда добавим i18n позже (i18n —
структурное изменение, дешевле сразу).

### B — Полный next-intl с `[locale]` pathname-prefix (отвергнут для Phase 4.12)

Канонический next-intl с `/en/...` и `/ru/...` URL'ами:

- ✅ SEO-friendly (уникальные URL'ы на язык);
- ✅ canonical для каждого языка отдельно;
- ❌ структурный refactor: переезд всех страниц под `app/[locale]/...`,
  включая 15+ auth-protected (persons, dna, trees, ...), переписывание
  `Link` компонентов (`useRouter()` на `next-intl/navigation`), правка
  тестов;
- ❌ scope-creep для Phase 4.12, который и так на 10 deliverables.

Откладываем на **Phase 4.13** (см. §«Когда пересмотреть»).

### C — i18n через cookie + middleware locale detection (выбран)

Минимальная foundation, на которой можно построить дальнейший pathname-
prefix без переписывания текстов:

- 📦 next-intl установлен.
- 🍪 Locale хранится в cookie `NEXT_LOCALE` (1 год).
- 🌐 Middleware (`apps/web/src/middleware.ts`) при первом визите
  detecter'ит locale из `Accept-Language` и фиксирует cookie.
- 📝 `messages/{en,ru}.json` — namespace по странице (`common`,
  `landing`, `demo`, `onboarding`, `pricing`).
- 🔄 LocaleSwitcher (en ↔ ru) в hero-section перепосылает cookie
  и перезагружает страницу.
- ⚠️ URL'ы НЕ префиксуются — `/`, `/demo` остаются собой; canonical
  приходится единым (нюанс для SEO, см. §«Последствия / риски»).

**Плюсы:** zero migration cost для существующих pages, foundation
готова к промоушу на pathname-prefix без переписывания строк.
**Минусы:** один canonical URL на оба языка → меньше SEO-сигнала
для русской выдачи в Phase 4.12; Phase 4.13 это исправит.

### D — Landing + onboarding на отдельной поддомене (отвергнут)

`marketing.autotreegen.com` для лендинга, `app.autotreegen.com` для
приложения. Плюсы: разделение деплоев. Минусы: лишний домен,
ломаем `same-origin` для cookies (auth, locale), сложнее SEO для
основного бренда. Не нужно нам сейчас.

## Решение

Принят **Вариант C — cookie-based i18n + всё-в-одном Next.js app**.

### Маркетинговые страницы

| Route | Назначение | Indexable |
|---|---|---|
| `/` | Landing: hero, 4 value-props, screenshots, pricing teaser, waitlist | ✅ |
| `/demo` | Read-only sample tree (hardcoded synthetic data) | ✅ |
| `/pricing` | Полная pricing table (Free / Pro) + FAQ | ✅ |
| `/onboarding` | 3-step wizard для нового user'а (после sign-up) | ✅ (но auth Phase 4.13 уведёт) |

### Onboarding state machine

`apps/web/src/lib/onboarding-machine.ts` — pure-function reducer,
тестируется отдельно от UI. Шаги:

1. **choose-source** — GEDCOM upload / FamilySearch / blank tree.
2. **import** — деталь импорта (file picker / OAuth redirect / tree name).
3. **done** — терминальный, CTA в dashboard.

Backend-зависимости (POST /trees для blank, deep-link в Phase 3.5
GEDCOM import, Phase 5.1 FamilySearch flow) отложены до Phase 4.13 —
здесь только UI-state.

### Empty-state redirect

`/dashboard` server-component вызывает `getCurrentUserTreesCount()` →
если 0 → `redirect("/onboarding")`. Phase 4.12 helper — placeholder
(всегда 0); Phase 4.10/4.13 заменят на real fetch без переписывания
страницы.

### Lead capture (POST /waitlist)

- ORM: `WaitlistEntry` (`packages/shared-models/.../waitlist_entry.py`).
- Migration: `0014_waitlist_entries.py`.
- Backend: `POST /waitlist` в parser-service (без auth, public).
  Pydantic `EmailStr` валидация, lower-case email, idempotent на
  duplicate (200 без mutation, чтобы анти-enumeration).
- Frontend proxy: `apps/web/src/app/api/waitlist/route.ts` →
  parser-service (same-origin для frontend, hide internal URL).
- **Privacy:** email НЕ логируется (только locale + source — anti-PII).
- Anti-abuse rate-limit отложен на Phase 13.x (Cloud Armor).

### SEO

- `apps/web/src/app/sitemap.ts` — динамический sitemap.xml для
  marketing-роутов; auth-protected исключены.
- `apps/web/src/app/robots.ts` — Allow на marketing, Disallow на
  `/api/`, `/persons/`, `/trees/`, `/dna/`, `/sources/`, `/hypotheses/`,
  `/familysearch/`, `/settings/`, `/dashboard`.
- Per-route `metadata` + `generateMetadata()` для динамических
  заголовков и Open Graph.
- `metadataBase` с `NEXT_PUBLIC_SITE_URL` в `RootLayout`.

### Perf budget (Lighthouse)

| Метрика | Цель |
|---|---|
| Performance | ≥ 90 |
| Accessibility | ≥ 95 |
| Best Practices | ≥ 95 |
| SEO | ≥ 95 |

Чтобы держать perf:

- Скриншоты — inline SVG-плейсхолдеры (LCP candidate без
  network round-trip). Перед public launch'ем owner подменит на
  `<Image>` с `priority`-flag для hero, `loading="lazy"` для остальных.
- Шрифт — system-ui (нет webfont download).
- Tailwind generation — JIT, бандл < 50 KB.
- next-intl messages — async-imported (только активная локаль в
  бандле).

### Linking leads to users

Когда юзер регистрируется (Phase 4.10), на signup hook'е сравниваем
`users.email` с `waitlist_entries.email` (lowercase). Совпадение —
помечаем источник «waitlist» в user.provenance, удаляем из waitlist.
Реализация — в Phase 4.10 PR (один SQL JOIN в signup pipeline).

## Последствия

**Положительные:**

- Главная страница больше не выглядит как «coming soon».
- Русско-говорящая аудитория получает нативный UX с первой минуты.
- Onboarding wizard проектирует «1 файл = дерево за 60 секунд» поток.
- Waitlist даёт маркетинговую базу до публичного launch'а.
- SEO-foundation позволит Phase 4.13 быстро поднять трафик из organic.

**Отрицательные / стоимость:**

- Cookie-based i18n без pathname-prefix → одинаковый canonical для
  ru и en → русская выдача в Google слабее. Mitigation: Phase 4.13
  промоутит на `[locale]` segments.
- 10 deliverables в одном PR — большой review surface. Mitigation:
  ADR-0035 фиксирует контракты, тесты разделяют слои.
- Скриншоты-placeholder'ы — owner подменит до launch'а; до тех пор
  landing выглядит «product-not-yet-shipped». Acceptable trade-off.
- `next-intl` добавляет ~12 KB к shared chunk. Acceptable.

**Риски:**

- **Auth не подключён → /onboarding пуб­лично доступен.** Фикс: Phase
  4.10 закрывает /onboarding и /dashboard под Clerk middleware. До
  тех пор — это разработческий «глаз», не security-issue: user
  ничего не «загружает», только видит UI без бэкенда.
- **Waitlist enumeration.** Идемпотентный 200 на duplicate скрывает,
  есть ли email в БД — anti-enumeration. Но timing-attack возможен.
  Mitigation: добавить constant-time response в Phase 13.x.
- **i18n будущая миграция на pathname-prefix.** Контракт
  `useTranslations(...)` совместим с обоими подходами; миграция —
  только routing layer. Стоит ~1-2 дня в Phase 4.13.

## Когда пересмотреть

- **Phase 4.10 merged** → /dashboard и /onboarding получают auth-guard;
  `getCurrentUserTreesCount` возвращает реальное число; signup hook
  link'ует waitlist email → user.
- **Phase 4.13 (next-intl pathname-prefix)** → URL'ы становятся
  `/ru/...` для русских; `[locale]` segment, обновляем canonical.
  Cookie остаётся как fallback на первый визит.
- **Lighthouse perf < 90** на real screenshots → переход на
  `<Image>` с `priority`/`loading` директивами; возможно — extract
  hero-images в `next/image-loader` с CDN.
- **Waitlist > 10 000 записей** → отдельная таблица с partitioning по
  `created_at` + Cloud SQL index review.
- **Marketing нужны utm-кампании** → `source` поле в `WaitlistEntry`
  заменяется на структурированный jsonb `{utm_source, utm_medium, utm_campaign}`.
- **Реальные screenshots готовы** → подменяем inline-SVG на
  `<Image src={...} priority />`.

## Ссылки

- Связанные ADR:
  - ADR-0010 (web-first slice) — общая Next.js / shadcn / pnpm база.
  - ADR-0011 (familysearch client) — onboarding step 2 «FamilySearch»
    deep-link'ит сюда.
  - ADR-0027 (FS token storage) — фоном привязан к onboarding flow.
  - Будущий ADR-0036 (Phase 4.10 — Clerk auth wiring).
- ROADMAP §8 (Phase 4 — Веб-сайт MVP), §8.1 (Страницы) с обновлённым
  Phase 4.12 row.
- Внешние:
  - [next-intl docs](https://next-intl-docs.vercel.app/) — runtime
    config + cookie-based locale.
  - [Next.js sitemap.ts / robots.ts](https://nextjs.org/docs/app/api-reference/file-conventions/metadata)
  - Lighthouse Web Vitals targets — Google «good»: LCP < 2.5s,
    INP < 200ms, CLS < 0.1.
