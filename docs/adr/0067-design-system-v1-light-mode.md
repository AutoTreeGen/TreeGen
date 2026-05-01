# ADR-0067: Design System v1 — 3D modern iconography, PT Serif, light-mode only

- **Status:** Accepted
- **Date:** 2026-05-01
- **Authors:** @autotreegen
- **Tags:** `design-system`, `iconography`, `typography`, `light-mode`, `phase-ds-1`

## Контекст

До этого PR в репозитории жил расщеплённый дизайн: untracked `design-tokens/`,
`design-prompt.md`, `logo-brief/` (черновики), а в `apps/web` — самописные
brand-токены в `globals.css` без единого источника правды; `apps/landing`
вообще не имел app-shell'а. Параллельно созревал ZIP-пакет
«AutoTreeGen Design System» (3.6 MB, 60+ файлов): 24 канонические иконки в
языке «3D modern», PT Serif как display-шрифт, локированная палитра
purple-and-archive, шесть категориальных tints для backdrop'ов иконок,
HTML-превью каждого token-кластера, два UI-кита (marketing + web-app),
SKILL.md для агентского skill-loader'а Claude Code.

Аудит ZIP перед merge'ем выявил **семь spec-несостыковок** (см. §«Что
зафиксировал v1»). Параллельно owner закрыл старое решение по dark-mode:

> **2026-05-01:** «light-mode only V1. Тёмная вариация откладывается до v2».

Причины:

- ICP проекта (AJ-genealogy researchers, 35–65) использует light-канвасы
  по умолчанию (museum collection software, не consumer dashboard).
- Dark variant удваивает калибровку shadow'ов, gradient-backdrop'ов
  иконок и контраста haplogroup-цветов — с одним theme'ом
  визуальный язык можно зафиксировать однозначно.
- 6 dev-дней сэкономлено vs. полный dual-theme контракт; v2 пересмотрит,
  если dark-mode-запросов >N за 6 месяцев.

ICP-fit для AJ-аудитории: archival-toned light surfaces читаются как
«музейная карточка», dark-mode «night-research» — отдельная проблема,
не релиз-блокер.

## Рассмотренные варианты

### A. Iconography language

- **3D modern (выбрано).** 24 chunky soft-body SVG объекта на 1:1 rounded
  card с radial-gradient backdrop'ом. Шесть категориальных tints,
  девять `b3*` радиальных градиентов (`b3Pink / b3Cyan / b3Mint / b3Gold /
  b3Coral / b3Plum / b3Cream / b3Wood / b3Paper`), unified `cy=85`
  ground-эллипс. Lucide остаётся для tiny inline UI affordances.
  - ✅ Узнаваемая фирменная подача, отличается от generic SaaS lucide-only.
  - ✅ Эстетика «museum object» совпадает с product positioning.
  - ❌ Дороже добавлять иконки (нужно следовать рецепту defs).
- **Lucide-only.** Стандартный stroke 24×24 currentColor.
  - ✅ Дешевле, быстрее.
  - ❌ Не отличает бренд от любого generic SaaS.
- **Helix-chromatic** (DNA-style градиенты на каждом glyph'е).
  - ❌ Чтение перегруженное, не читается на mobile, бьётся с
    haplogroup-цветами в tree-визуализациях.

### B. Display typeface

- **PT Serif (выбрано).** Vendored locally в `/fonts` (4 ttf:
  Regular/Italic/Bold/BoldItalic). Транзисционный serif с museum-catalogue
  bones.
  - ✅ Институциональный голос, отличается от санс-сетов SaaS-конкурентов.
  - ✅ Open Font License — без legal-cost; vendored = offline-OK.
  - ❌ +200KB на font-payload (acceptable для researcher-ICP с broadband).
- **Manrope** (изначально в SKILL.md ZIP'а).
  - ❌ Generic geometric sans, не отличается; не vendored — Google Fonts
    egress.
- **Inter / Inter Display.** Уже есть как body-семейство.
  - ❌ Один шрифт без visual hierarchy между display и body.

### C. Dark mode policy

- **Light only — V1 (выбрано).** Один canvas, одна shadow-калибровка.
  - ✅ 6 dev-дней экономии; визуальный язык фиксируется однозначно.
  - ❌ Часть researcher-ICP пользуется dark-mode привычно — отложено.
- **Dual theme.** Полный `[data-theme="dark"]` block, calibration shadows.
  - ❌ Двукратная стоимость calibration; рисуем 24 иконки на двух canvas'ах.
- **System-driven.** `prefers-color-scheme: dark` MQ.
  - ❌ Без light-default-fallback теряем control; user-toggle лучше для v2.

### D. DRY: один источник design-токенов vs. копия в apps/{web,landing}

- **Копия в apps/web/src/styles/ + apps/landing/src/styles/ + repo root
  (выбрано для v1).** Три копии `colors_and_type.css` / `design-system.css`.
  - ✅ Каждое приложение self-contained: build не нуждается в cross-app
    workspace-resolver.
  - ✅ Standalone preview/*.html работает (root-копия + ./fonts/).
  - ❌ Drift возможен — митигация: `tests/test_design_system_consistency.py`
    проверяет, что ключевые инварианты (PT Serif, нет dark-mode, b3Paper)
    держатся в обеих app-копиях.
- **Workspace member `packages-js/design-system/`** — V2.
  - ✅ Single source of truth, импорт через `@autotreegen/design-system`.
  - ❌ Требует pnpm workspace setup для new package, build-tool changes,
    миграция всех @import. Откладываем до v2 после feedback с реального
    dogfooding'а.

### E. SKILL.md placement

- **`.claude/skills/design-system/SKILL.md` (выбрано).** Front-matter
  совместим с Claude Code skill-loader'ом; путь конвенциональный.
  - ✅ Authoring agents подхватывают design-system автоматически.
- **Repo root.** Альтернатива, owner оставил на моё усмотрение.
  - ❌ Засорит root; SKILL — agent-tooling, не общий project-doc.

## Решение

**Adopt:**

- **3D modern iconography** в `preview/brand-iconography.html` (24 иконки) +
  `preview/brand-icon-style-spec.html` (рецепт). 9 `b3*` градиентов,
  6 backdrop tints, unified `cy=85` ground.
- **PT Serif** vendored локально в `/fonts/` (root) + `apps/{web,landing}/
  public/fonts/` (3 копии для standalone preview + Next.js `/fonts/`).
- **Light theme only — V1.** Без `[data-theme="dark"]` блоков, без
  `prefers-color-scheme: dark` media-queries, без `.dark` class-селекторов.
- **Категориальный backdrop tint mapping** (не визуальная случайность):
  t1 pink / t2 cyan / t3 gold / t4 mint / t5 plum / t6 coral.
- **3 копии `design-system.css`** для v1 — repo root, `apps/web/src/styles/`,
  `apps/landing/src/styles/`. Apps-копии используют абсолютные `/fonts/`
  (Next.js public/), root-копия использует `./fonts/` для preview/.
- **`SKILL.md` в `.claude/skills/design-system/`** — convention-fit для
  Claude Code agents.

**Spec-fixes, зафиксированные в этом PR (DS-1):**

| # | Что зафиксировано |
|---|---|
| 1 | SKILL.md font corrected: Manrope → PT Serif (vendored ttf) + Inter + JetBrains Mono. |
| 2 | `brand-icon-style-spec.html` defs-блок раскрыт (был псевдо-код «...5 stops...»); inline-defs обязательны для нового icon page. |
| 3 | `.t5` (plum) и `.t6` (coral) добавлены к 4 существующим tint-классам в spec; reference grid 4 → 6 cells, по одному примеру на tint. |
| 4 | `brand-iconography-3d-modern.html` (byte-identical duplicate of `brand-iconography.html`) удалён; ссылки в README + SKILL зачищены. |
| 5 | 9-й swatch `b3Paper` добавлен в spec; README palette listing обновлён. |
| 6 | Section «Backdrop tint mapping» добавлена; 7 иконок переклассифицированы по category mapping (NOTE/QUESTION → t5, REFRESH → t2, PEDIGREE → t4, HOUSE → t4, SUITCASE → t3, CALENDAR → t3). |
| 7 | Все 24 иконки `cy="85"` (было 50/50 split между cy=84 и cy=86); spec-текст «cy ≈ 86» исправлен. |

## Последствия

### Положительные

- Авторов агентов и контрибьюторов фиксирует один skill-документ
  (`SKILL.md`), один token-источник (`colors_and_type.css`), один canonical
  icon set (`preview/brand-iconography.html`). Halt-and-clarify auditing
  становится дешевле — есть test-suite для drift.
- Apps `apps/web` и `apps/landing` стартуют с identical brand-палитрой;
  marketing-site и product не разъезжаются по vibe.
- Light-only commitment экономит 6 dev-дней до релиза; одна shadow-калибровка
  и один контраст-target (WCAG AAA на Snow).
- 3D modern иконки — ICP-differentiator vs. generic SaaS lucide-only
  оформление; узнаваемость бренда поднимается без custom illustration cost.
- `tests/test_design_system_consistency.py` ловит regression на 5
  инвариантах (PT Serif, b3Paper, no-dark-mode, no-3d-modern-duplicate,
  cy=85) — drift невозможен незаметно.

### Отрицательные / стоимость

- **DRY violation.** Три копии `colors_and_type.css` / `design-system.css`
  (root + 2 apps). Acceptable v1 trade-off; v2 промоутит в
  `packages-js/design-system/` workspace member.
- **Font payload.** PT Serif добавляет ~840KB ttf на каждое приложение
  (4 cuts × ~200KB). Mitigation: `font-display: swap` уже включён;
  Inter+JetBrains Mono остаются на Google Fonts CDN.
- **Dark-mode debt.** Часть researcher-ICP пользуется dark-mode по
  привычке. Mitigation: roadmap-запись «revisit dark variant after 100+
  active users / 6 месяцев / >N dark-mode requests» в this ADR.
- **Iconography расширяемость.** Добавление новой иконки требует следовать
  рецепту (defs inline, key-light 0.32×0.22, cy=85, category-tint
  mapping). Mitigation: `brand-icon-style-spec.html` — авторитетная
  recipe-документация; новый brief DS-3 (V2) добавит ESLint-rule на
  lucide-enforcement в product UI чтобы случайно не расползалось.

### Риски

- **Drift между 3 копиями `design-system.css`.** Severity: medium.
  Mitigation: pytest-набор `test_design_system_consistency.py` проверяет
  всех три копии на ключевые инварианты; CI-job ловит расхождение.
- **Vendored ttf legal status.** Severity: low. Mitigation: PT Serif
  под Open Font License — vendoring разрешён, attribution требуется
  (есть в README §Sources).
- **PT Serif rendering на Windows Chromium pre-100.** Severity: low.
  Mitigation: serif-fallback chain (Iowan Old Style → Source Serif Pro
  → Georgia) гарантирует читаемость.
- **`prefers-color-scheme` browser default может вызвать unwanted dark on
  hardware OS-dark-mode users.** Severity: low. Mitigation: `html {
  color-scheme: light; }` фиксирует scheme; user-agent дёргает только
  светлый default.

## Когда пересмотреть

- **Active users > 100 / 6 месяцев passed / dark-mode requests > 20 в
  feedback** → открыть V2 brief на dual-theme.
- **Cross-app drift detected** между копиями `design-system.css` →
  миграция в `packages-js/design-system/` workspace member, single
  `@import "@autotreegen/design-system/tokens"`.
- **Иконка добавлена с нарушением tint mapping** (через PR-review caught) →
  обновить spec mapping или принять новую category в этой ADR.
- **Lucide расползлось в brand-facing surfaces** (marketing hero / empty
  states / feature cards) → DS-3 ESLint-rule на lucide-enforcement
  становится релиз-blocker'ом.

## Ссылки

- **Files:**
  - `.claude/skills/design-system/SKILL.md` — agent skill loader
  - `colors_and_type.css` — root token source (preview-compatible)
  - `apps/web/src/styles/design-system.css` — apps copy (Next.js /fonts/)
  - `apps/landing/src/styles/design-system.css` — apps copy
  - `preview/brand-iconography.html` — canonical 24-icon set
  - `preview/brand-icon-style-spec.html` — recipe + inline defs
  - `assets/logo/` — vector logo system
  - `tests/test_design_system_consistency.py` — drift guard
- **Связанные ADR:**
  - ADR-0066 (mobile-responsive — Phase 4.14a) — touch-action / no-zoom
    rules применяются поверх DS-1 токенов.
  - V2 (TBD) — `packages-js/design-system/` workspace migration; DS-3
    lucide-enforcement ESLint rule.
- **DS-1 brief:** см. PR-описание для full owner directives + 7 fix list.
