# ADR-0011: Brand system v1.0 — визуальная идентичность

- **Status:** Accepted
- **Date:** 2026-04-26
- **Authors:** @vladimir
- **Tags:** `brand`, `design-system`, `frontend`, `marketing`

## Контекст

Платформа выходит из чисто-инженерной стадии в фазу публичного присутствия:
лендинг (`apps/landing`) уже опубликован, готовится app (`apps/web`), социальные
превью и иконки нужны для шаринга и PWA-инсталляции. Параллельно владелец
сформулировал визуальную идею бренда — медальон с деревом и ДНК-спиралью,
тёмная фиолетовая палитра, Montserrat — и оформил это как **Brand
Guidelines v1.0** (PDF, 5 страниц).

Нужно зафиксировать визуальную систему как версионируемый артефакт в репозитории
и определить, как код-релизы соотносятся с релизами бренда.

## Рассмотренные варианты

### Вариант A — Только PDF + ссылка из README

- ✅ Минимум работы, статичная картинка-референс.
- ❌ Бренд не машинно-читаем — токены приходится копипастить.
- ❌ Нет проверяемых SVG-исходников, фронтенд-разработка зависит от ручного
  подбора цветов.
- ❌ Невозможно версионировать цветовые/шрифтовые правки атомарно.

### Вариант B — Полный design system как отдельный пакет (`packages-js/brand`)

- ✅ Чистое разделение: бренд независим от приложений.
- ✅ Импорт из любого фронта одним package-ref.
- ❌ Преждевременно для текущей стадии: нет ни одного потребителя кроме
  `apps/landing`, накладные расходы на пакет/публикацию.
- ❌ Pnpm workspace `packages-js/*` пока пустой — пришлось бы скаффолдить
  отдельную инфру.

### Вариант C — Папка `assets/brand/` как single source of truth

- ✅ Все исходники (PDF, SVG, PNG, ICO, токены) в одном месте.
- ✅ Машинно-читаемые `tokens.css` импортируются напрямую через Tailwind 4
  `@import` без бандлинга.
- ✅ Версионируется обычным git + тегами `brand-vX.Y` (как и предписывает
  сам гайдлайн).
- ✅ Низкая инфраструктурная стоимость — можно мигрировать в `packages-js/brand`
  позже, когда появится второй потребитель.
- ❌ Импорты идут по относительным путям (`../../../../assets/brand/...`).

## Решение

Принят **Вариант C**: визуальная идентичность v1.0 хранится в `assets/brand/`.

Структура папки совпадает с рекомендованной самим гайдлайном (см. PDF p.5):

```
assets/brand/
├── tokens.css                 # Tailwind 4 @theme + plain CSS-переменные
├── 01-logo/
│   ├── mark/                  # autotreegen-mark.svg (медальон)
│   ├── wordmark/              # autotreegen-wordmark.svg (текст)
│   ├── horizontal/            # autotreegen-horizontal.svg (mark + wordmark)
│   └── mono/                  # mark-mono-black.svg, mark-mono-white.svg
├── 02-favicon/                # favicon.svg, favicon.ico, favicon-{16,32,48,64}.png
├── 03-app-icons/              # apple-touch-icon, icon-192/512, maskable-512
└── 04-social/                 # og-image.svg/png, twitter-card.png, avatar-400.png
```

### Палитра v1.0 (источник: Brand Guidelines p.3)

| Token | HEX | Назначение |
|---|---|---|
| `brand-deep-purple` | `#2C1054` | Основной фон, медальон |
| `brand-royal-purple` | `#4B2D8C` | Текст логотипа, заголовки |
| `brand-blue` | `#2E5DA5` | Акцент, конец градиента |
| `brand-lilac` | `#B070C0` | Иконки, теги, акценты |
| `brand-light-lilac` | `#E8D5F0` | Фоны, плашки, разделители |
| `brand-ink` | `#1A1024` | Базовый текст |
| `brand-snow` | `#FFFFFF` | Фон, инверсия |

### Шрифт

**Montserrat** (SIL OFL 1.1, бесплатно) → Inter → system-ui. На `apps/landing`
оставлен **Geist** (pre-v1.0 решение) — миграция на Montserrat вынесена в
отдельную follow-up задачу, чтобы не ломать живой визуал лендинга одним
коммитом.

### Логотип — stand-in vs. оригинал

PDF-гайдлайн содержит AI-сгенерированный медальон, который **не воспроизводится
1:1 как чистый SVG** без доступа к исходнику дизайнера. Текущие SVG в
`assets/brand/01-logo/` — это **stand-in v1.0**: чистая векторная интерпретация
тех же элементов (тёмно-фиолетовый медальон, силуэт дерева, ДНК-спираль вместо
ствола). Визуально близко, но не идентично PDF.

Когда появится оригинальный исходник (Figma / Illustrator), stand-in будет
заменён — это станет brand v1.1 с новым тегом.

### Версионирование

- Любое изменение визуальной системы → bump версии в `docs/brand/README.md`,
  обновление PDF (если применимо), commit `feat(brand): ...` или
  `docs(brand): ...` + git-тег `brand-vX.Y`.
- Code-релизы и brand-релизы независимы. Brand-теги имеют префикс `brand-`,
  чтобы не пересекались с product-тегами.
- Backwards-compatibility: token names (`--color-brand-royal-purple` и т.п.)
  сохраняем, чтобы изменение цветов не ломало код. Если токен переименовывается
  — сначала alias, потом deprecation (минимум 1 минор-релиз).

## Последствия

**Положительные:**

- Бренд-токены доступны как Tailwind утилиты (`text-brand-royal-purple`,
  `bg-brand-deep-purple`, `bg-brand-hero`, `text-brand-wordmark`) во всех
  фронтах через единственный `@import`.
- Социальные превью (OG, Twitter card, маскируемая иконка) генерируются
  из тех же SVG детерминированно через ImageMagick — реплицируемый процесс.
- PDF-гайдлайн остаётся source-of-truth для не-разработчиков (дизайнеры,
  внешние подрядчики).

**Отрицательные / стоимость:**

- Дубль токенов на лендинге: pre-v1.0 violet-scale (50–900) + warm-cream canvas
  ещё используется во многих компонентах. Полная миграция → отдельная задача.
- SVG-логотипы текущей итерации — stand-in, не финальные.
- `og-image.png`, `apple-touch-icon.png` и т.д. рендерятся из SVG локально;
  build-step CI пока их не пересобирает (только что сгенерированные ассеты
  закоммичены).

**Риски:**

- Несоответствие AI-генерированного PDF-логотипа и SVG-stand-in может вызвать
  визуальную путаницу для внешних потребителей бренда. Митигация: PDF
  помечен как «визуальный референс», SVG — как production-asset.
- `<style>`-блоки внутри SVG не всегда совместимы с raster-конвертерами
  (ImageMagick + libvgsvg). Текущие SVG используют атрибуты вместо `<style>`
  для совместимости. При генерации новых ассетов держать это в уме.

**Что нужно сделать в коде:**

- ✅ `assets/brand/tokens.css` создан и подключен в `apps/landing/globals.css`.
- ✅ Favicon-комплект, apple-touch, icon-192/512, maskable, og-image
  скопированы в `apps/landing/public/`, прописаны в `layout.tsx` metadata.
- ✅ `manifest.webmanifest` для PWA.
- 🔜 Follow-up: миграция `apps/landing/src/components/logo.tsx` на brand v1.0
  токены (заменить violet-gradient на royal-purple→blue).
- 🔜 Follow-up: подключение Montserrat через `next/font/google`.
- 🔜 Follow-up: удаление `apps/landing/public/favicon-pre-v1.svg.bak` после
  визуального ревью.
- 🔜 Follow-up: получить оригинальный исходник логотипа (Figma) → brand v1.1.

## Когда пересмотреть

- Переезд на сторонний design-system (Radix Themes, daisyUI и т.п.).
- Появление второго потребителя бренд-ассетов (apps/web как production app)
  → возможный рефактор в `packages-js/brand`.
- Ребрендинг или major-визуальный рестайл → brand v2.0, новый ADR.

## Ссылки

- Brand Guidelines PDF: [`docs/brand/AutoTreeGen-BrandGuidelines-v1.0.pdf`](../brand/AutoTreeGen-BrandGuidelines-v1.0.pdf)
- Brand README (машинно-читаемые токены): [`docs/brand/README.md`](../brand/README.md)
- Brand assets: [`assets/brand/`](../../assets/brand/)
- Связанные ADR: ADR-0002 (структура монорепо).
