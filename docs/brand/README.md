# AutoTreeGen Brand Assets

Source of truth — `AutoTreeGen-BrandGuidelines-v1.0.pdf` (this folder).
Полный гайдлайн (логотип, защитная зона, правила «можно / нельзя») — в PDF.
Этот README — машинно-читаемая выжимка токенов для кода.

> **Версионирование.** Любое изменение визуальной системы — новый PDF + bump версии
> в этом README + git-тег `brand-vX.Y` (см. CLAUDE.md секция 8 / ROADMAP).

Текущая версия: **1.0** (April 2026).

---

## Color tokens

| Token | Role | HEX | RGB | CMYK | Pantone (≈) |
|---|---|---|---|---|---|
| `brand.deep-purple` | Primary · фон, медальон | `#2C1054` | 44, 16, 84 | 48, 81, 0, 67 | — |
| `brand.royal-purple` | Primary · текст логотипа, заголовки | `#4B2D8C` | 75, 45, 140 | 46, 68, 0, 45 | PMS 2685 C |
| `brand.blue` | Primary · акцент, конец градиента | `#2E5DA5` | 46, 93, 165 | 72, 44, 0, 35 | PMS 2945 C |
| `brand.lilac` | Secondary · иконки, теги, акценты | `#B070C0` | 176, 112, 192 | 8, 42, 0, 25 | PMS 2573 C |
| `brand.light-lilac` | Secondary · фоны, плашки, разделители | `#E8D5F0` | 232, 213, 240 | 3, 11, 0, 6 | — |
| `brand.ink` | Neutral · базовый текст | `#1A1024` | 26, 16, 36 | 28, 56, 0, 86 | — |
| `brand.snow` | Neutral · фон, инверсия | `#FFFFFF` | 255, 255, 255 | 0, 0, 0, 0 | — |

> Pantone — приближённое соответствие. Перед типографской печатью утверждать по
> Pantone-веером: экранные RGB и печатные CMYK расходятся.

### Tailwind 4 (`@theme` снippet)

```css
@theme {
  --color-brand-deep-purple: #2C1054;
  --color-brand-royal-purple: #4B2D8C;
  --color-brand-blue: #2E5DA5;
  --color-brand-lilac: #B070C0;
  --color-brand-light-lilac: #E8D5F0;
  --color-brand-ink: #1A1024;
  --color-brand-snow: #FFFFFF;
}
```

### CSS-переменные (для shadcn/ui токенов)

```css
:root {
  --brand-deep-purple: 268 68% 20%;     /* HSL для shadcn-схемы */
  --brand-royal-purple: 261 51% 36%;
  --brand-blue: 215 57% 41%;
  --brand-lilac: 287 39% 60%;
  --brand-light-lilac: 281 56% 89%;
  --brand-ink: 264 38% 10%;
}
```

---

## Typography

| Role | Family | Size | Weight | Tracking | Notes |
|---|---|---|---|---|---|
| Display / H1 | Montserrat | 32–48 pt | 800 | −1% | Hero, page titles |
| Section / H2 | Montserrat | 14–18 pt | 700 | 0 | — |
| Subhead / H3 | Montserrat | 11–13 pt | 600 | 0 | — |
| Body | Montserrat | 9–11 pt | 400 | 0 | line-height 1.5 |
| Caption | Montserrat | 7–8 pt | 700 | +15% | UPPERCASE метки |

**Font stack:** `Montserrat, Inter, system-ui, -apple-system, "Segoe UI", sans-serif`

Лицензия: **SIL Open Font License 1.1** (бесплатно для коммерческого использования).
Источник: <https://fonts.google.com/specimen/Montserrat>.

---

## Logo

Варианты в исходниках: `Horizontal` (primary), `Mark-only`, `Wordmark`, `Mono`
(black / white / single-color).

| Правило | Значение |
|---|---|
| Минимальная ширина (горизонтальный) | **30 мм / 120 px** |
| Ниже минимума | использовать `mark-only` (плоская версия) |
| Защитная зона | `x` со всех сторон, где `x` = высота заглавной «A» в wordmark |
| Favicon / иконки < 64 px | плоская версия `mark-only` |
| Печать | только SVG / PDF (vector). Растр для печати — запрещено |

### Запрещено

Менять цвета · растягивать / сжимать / наклонять / вращать · добавлять тени, обводки,
фильтры · размещать на пёстром фоне без подложки · использовать растр для печати.

---

## File system (brand assets)

Структура папки `autotreegen-brand/` (когда исходники появятся в репо или DVC):

```
autotreegen-brand/
├── 01-logo/
│   ├── horizontal/    (svg, pdf, png-1x/2x/3x)
│   ├── mark-only/
│   ├── wordmark/
│   └── mono/          (black, white, single-color)
├── 02-favicon/        (ico, icns, png 16–512)
├── 03-app-icons/      (ios, android, pwa, maskable)
├── 04-social/         (avatar 400×400, og-image 1200×630)
├── 05-print/          (cmyk pdf, pantone-spec)
└── 06-source/         (ai, fig — рабочие исходники)
```

| Формат | Назначение | Размеры / варианты |
|---|---|---|
| SVG | Web, презентации, печать малых тиражей | Horizontal / Mark-only / Wordmark / Mono |
| PDF (vector) | Профессиональная печать, документы | CMYK + Pantone версии |
| PNG @1×/2×/3× | Web, mobile UI, email-подписи | 240 / 480 / 720 px ширина |
| ICO / ICNS | Favicon, app icon (Windows / macOS) | 16, 32, 48, 64, 128, 256, 512 |
| App icon | iOS / Android / PWA | 1024×1024 master + maskable adaptive |
| Social | Avatar для соцсетей и GitHub | 400×400 — только знак, квадрат |

**Где лежат исходники сейчас:** TBD — на момент v1.0 в репозитории есть только
этот PDF. Задача на будущее: создать `assets/brand/` (или DVC-tracked), залить
SVG/PNG/ICO, обновить ссылки в этом README.

---

## Changelog

- **1.0** (2026-04) — первая версия. Логотип, палитра (7 токенов), типографика
  (Montserrat), файловая структура. Источник истины — PDF в этой папке.
