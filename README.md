# AutoTreeGen Design System

> Evidence-based genealogy. Every claim cited, every branch traced.

This system is the visual + interaction language for **AutoTreeGen** — an
AI-powered platform that unifies GEDCOM family trees, DNA test results
(autosomal, Y-DNA, mtDNA haplogroups), and historical archive sources into a
single research workspace. Every fact in the system is bound to a citation,
a confidence score, and a provenance trail.

## Audience

Serious researchers — professional genealogists, academic historians, and
DNA-driven family historians (35–65, 5+ years of experience). They have
outgrown Excel + GEDCOM chaos and need precision tooling. **Not casual
hobbyists.**

The brand sits adjacent to **Notion** (precision), **Linear** (density), and
**Are.na** (depth). It is explicitly **not** Ancestry / MyHeritage / 23andMe —
no smiling families, no "Discover your story!", no glow auras, no celtic
mysticism.

## Sources

This design system was built against:

- **GitHub:** `AutoTreeGen/TreeGen` (default branch `main`). Monorepo with
  `apps/web` (Next.js 15 product) and `apps/landing` (Cloudflare-Pages
  marketing site). Stack: Next.js 15 · React 19 · TypeScript strict · Tailwind 4
  · shadcn/ui · Clerk · `motion` · `lucide-react` · `react-d3-tree`.
- Backend (read for vocabulary only): FastAPI + Postgres; packages
  `gedcom-parser`, `dna-analysis`, `entity-resolution`, `inference-engine`.
- **Brand brief** with locked palette, type system, haplogroup colors, voice
  rules, and anti-patterns.
- Three concept logo PNGs in `uploads/` — used as **conceptual** reference
  only (the AI-rendered bevels / glows / sparkles in those mocks are explicit
  anti-patterns; the production mark in `assets/logo/` is a clean vector
  redraw of the concept).

## Index

| File / folder | What's in it |
|---|---|
| `README.md` | This file — context, content rules, visual foundations, iconography |
| `SKILL.md` | Agent-skill front matter so this folder works inside Claude Code |
| `colors_and_type.css` | CSS custom properties for color, type, spacing, radii, shadow, motion + base resets and semantic type styles. Single source of truth |
| `fonts/` | PT Serif (vendored ttf); Inter and JetBrains Mono via Google Fonts CDN |
| `assets/logo/` | Vector logo system — mark, horizontal, mono, favicon |
| `assets/icons/` | Icon usage notes — we use `lucide-react` from CDN |
| `assets/illustration/` | Brand illustrations — the haplogroup-ribbon tree |
| `preview/` | Card-sized HTML previews of every token group + component cluster (registered for the Design System tab) |
| `ui_kits/web-app/` | Product UI kit — tree explorer, person card, hypothesis panel, source citations, DNA match list |
| `ui_kits/marketing/` | Marketing-site UI kit — hero, features, pricing, footer |

## Content fundamentals

The product writes like a careful researcher's lab notebook, not a marketing
brochure. Every line carries weight; nothing is filler.

### Voice

- **Precise.** Use the technically correct word. `parent` not `family member`.
  `confidence 0.84` not `pretty sure`. `R1a-M198` not `Eastern European`.
- **Evidence-first.** Every product statement is followed by, or paired with,
  a citation, source, or confidence value. "Born 1847 in Vilnius **·
  Vilnius vital records, fond 728**" — not "Born 1847 in Vilnius."
- **Calm authority.** No hedging ("might possibly maybe"), no hype ("amazing",
  "incredible", "powerful"), no exclamation marks anywhere.
- **Sentence case** for headings, buttons, menu items, table headers — every UI
  surface. Proper nouns and acronyms keep their casing (GEDCOM, DNA, R1a).
- **Second person** for instructions ("Upload your GEDCOM"), **third person /
  passive** for system state ("3 conflicts detected", "Confidence dropped to
  0.62 after archive review").
- **No emoji.** Anywhere. Status uses dot indicators or labelled badges.
- **No marketing fluff.** No "transform your research", "unlock the past",
  "discover your story". The closest analogue is a methods section in a
  scientific paper.

### Examples

| ❌ Avoid | ✅ Prefer |
|---|---|
| Discover Your Family Story! | Evidence-based genealogy |
| Amazing AI-powered insights | Hypothesis engine with confidence scoring |
| Click here to upload | Upload GEDCOM |
| You might be related to… | DNA match · 47 cM shared across 3 segments |
| Powerful tree builder | Tree explorer · 14,231 persons indexed |
| Get started for free → | Sign up |
| Loading… | Computing IBD segments · 12 of 47 chromosomes |
| Oops! Something went wrong | Parse failed at line 1,284 — INDI tag missing GIVN |

### Casing on UI

- Buttons: `Sign up`, `Upload GEDCOM`, `Add citation`, `Merge persons`
- Nav items: `Tree`, `DNA`, `Hypotheses`, `Sources`, `Settings`
- Empty states: `No DNA kits uploaded yet.`
- Confirmations: `Person merged. 2 sources reattached.`
- Errors: lead with what failed and where, not with apology.

### Numbers and units

- Use **digits**, not words: `3 conflicts`, not "three conflicts".
- DNA in centimorgans: `47.2 cM` (one decimal), segments uppercase: `IBD`.
- Years bare: `1847–1912` (en-dash, no spaces).
- Confidence as decimal `0.84` in dense UI, percent `84%` in prose.
- Haplogroup names in mono: `R1a-M198`, `J2a-M410`.

## Visual foundations

The system is **purple-and-archive**. Deep purple surfaces, brand blue for
action, ink for text, paper-white snow for the working canvas. Lilac is a
tinting accent, never a fill. The look is restrained, scientific, archival —
closer to a museum collection-management system than a consumer DNA app.

> **Light theme only — V1 (owner decision 2026-05-01).** A dark variant is
> deferred to v2. Do not introduce dark surfaces, alternate-theme selectors,
> or system-theme media queries. v1 commits to a single light canvas so
> shadow tuning, gradient backdrops, and the iconography palette can be
> calibrated against one surface.

### Color

Locked palette (see `colors_and_type.css` for tokens):

- `#2C1054` **Deep Purple** — surfaces (sidebar, marketing hero). Always
  paired with high-contrast type.
- `#2E5DA5` **Brand Blue** — accent / links / primary CTA / focus ring. The
  one color that *acts*.
- `#1A1024` **Ink** — body text, deepest border (light mode only).
- `#B070C0` **Lilac** — used **only** at 10–20% as tints (highlight rows,
  selection, soft dividers). Never a fill.
- `#E8D5F0` **Light Lilac** — page backgrounds, soft cards.
- `#FFFFFF` **Snow** — surface / cards / overlays.
- Semantic: `#2D8C5C` success · `#C08A2D` warning · `#C0392D` danger.

Haplogroup colors (tree-visualization only — never UI chrome):

`#2E5DA5` R1a · `#C04B7E` J2 · `#4BB39E` H1a · `#C0904B` T1 ·
`#7E4BC0` E1b · `#4BC07E` I1 · `#C04B4B` G2 · `#4B7EC0` N1c.

Each haplogroup ribbon also carries a **label** and a **shape pattern**
(stripe / dot / cross-hatch) so color is never the sole carrier of meaning —
required for protanopia / deuteranopia / tritanopia.

### Type

- **PT Serif** 400 / 400 italic / 700 / 700 italic — display, headings
  (h1–h3 marketing; h1 product), and any place a literary, archival voice is
  wanted (pull quotes, source references). PT Serif's transitional bones
  carry institutional gravitas — closer to a museum catalogue than a SaaS
  landing page. Vendored locally as `.ttf` in `/fonts`.
- **Inter** 400 / 500 / 600 — body, UI, table cells, form labels.
- **JetBrains Mono** 400 / 500 — DNA codes (`R1a-M198`), GEDCOM tags, IDs,
  raw archive fond numbers, confidence values in dense tables.

Sizes are based on a 4px grid: `12 / 13 / 14 / 16 / 18 / 20 / 24 / 32 / 48 /
64`. Body default is 16px / 1.55 line-height. Body text targets WCAG AAA
contrast (≥7:1) on Snow.

### Spacing, radii, shadows

- **Spacing scale** (4-based): `2 4 8 12 16 20 24 32 40 48 64 80 96 128`.
  Component padding defaults: button `8 16`, card `20`, page gutter `24` mobile
  / `48` desktop.
- **Radii:** `sm 4` (chips, badges), `md 6` (buttons, inputs), `lg 10`
  (cards), `xl 16` (modals, hero panels), `full` (avatars, dot indicators).
  Never circle-ify rectangles for whimsy.
- **Shadows** — restrained, archival. Three levels:
  - `shadow-sm` — `0 1px 2px rgba(26,16,36,0.06)` on cards at rest.
  - `shadow-md` — `0 4px 12px rgba(26,16,36,0.08)` on hover / focus / sticky
    surfaces.
  - `shadow-lg` — `0 12px 32px rgba(26,16,36,0.14)` on modals only.
  - **No drop shadows for emphasis. No glow. No glassmorphism.**

### Backgrounds and texture

- The product canvas is **Snow** with hairline `--color-border` divisions —
  it should read as a researcher's worktop, not a dashboard.
- The marketing site uses **Light Lilac** as the page wash with **Deep
  Purple** sections for emphasis.
- **No gradients** as fills. The single allowed exception is the haplogroup
  ribbon visualization, where a per-ribbon gradient signals haplogroup mixing
  in admixed individuals.
- **No background images** outside of the haplogroup-ribbon tree
  illustration.
- **No textures, no grain, no noise.** The aesthetic is paper, not film.

### Motion

- **Easing:** `cubic-bezier(0.2, 0.8, 0.2, 1)` (out-cubic-ish) is the house
  curve. Linear only for indeterminate progress.
- **Durations:** `120ms` micro (hover, focus), `200ms` standard (panel show /
  hide), `320ms` deliberate (route change, tree-fit-to-screen).
- **Fades dominate.** Slides only when something is genuinely entering from a
  direction (drawer, toast). No bounces, no springs, no scale-up entry.
- Reduced-motion users get fade-only at 80ms.

### Hover, focus, press

- **Hover (background):** surface darkens by one step (`Snow → Light Lilac`,
  `Light Lilac → Lilac/15%`). Never lighten.
- **Hover (text link):** underline appears (4px offset). Color stays Brand
  Blue.
- **Focus:** 2px Brand Blue ring, 2px offset. Always visible. Never `outline:
  none` without a replacement.
- **Press (active):** shrink 98% on buttons, 99% on cards. No color change.
- **Disabled:** 50% opacity, `cursor: not-allowed`, no hover response.

### Borders, dividers

- 1px hairline borders in `--color-border` (light) / 1px in
  `--color-border-strong` for emphasis.
- Cards always have a border **and** a `shadow-sm` — neither alone is enough
  to seat them on the canvas.
- Table rows: bottom hairline. Never zebra stripes.

### Transparency and blur

- **Used sparingly.** Acceptable cases: command palette backdrop (Ink at
  60% + 8px blur), modal scrim (Ink at 50%, no blur). Everything else is
  opaque.

### Layout

- Desktop max content width: 1280px (product), 1120px (marketing).
- Sidebar: 240px collapsed-label, 64px icon-only.
- Page gutter: 48px desktop, 24px tablet, 16px mobile.
- Sticky elements: top bar (64px), inspector panels (right rail, 360px).
- Card density: hairline border + `shadow-sm` + 20px padding. Never shadow
  alone.

### Imagery

If used, photography is **archival-toned** — desaturated, cool, slightly
underexposed. Subjects: documents, maps, instruments, hands at desks. Never
faces, never families posing, never sunlit fields, never sepia-pastiche.

## Iconography — 3D modern

The brand's icon language is **chunky soft-body 3D objects, rendered as
inline SVG.** Each glyph is a real-feeling object — a heart, a clock, a
helix, a key — sitting on a 1:1 rounded card with a tinted radial-gradient
backdrop. Shading does the work: a warm key-light at the upper-left, a small
white specular highlight, an optional cool ambient bounce on the underside,
and a soft cast shadow on the ground plane. There is **no medallion / coin
container** behind the icon; the glyph itself carries the brand.

The canonical 24-icon set lives in `preview/brand-iconography.html`. The
recipe — gradient stops, specular params, defs to copy, anti-patterns —
lives in `preview/brand-icon-style-spec.html`. Reuse those `<defs>` verbatim
when adding a new icon; the palette of object gradients
(`b3Pink / b3Cyan / b3Mint / b3Gold / b3Coral / b3Plum / b3Cream / b3Wood / b3Paper`)
plus `b3Spec`, `b3Ambient`, `b3Hole`, and the `b3Shadow` filter cover
everything we've drawn so far.

**Anti-patterns:** no medallion / coin frame, no flat fills, no outlines on
bodies (interior strokes for chevrons / checks are fine), no helix-chromatic
palette, no glow auras, no emoji.

**Lucide is still allowed** for tiny inline UI affordances inside the product
where a 3D glyph would be over-rendered — chevrons, close, drag handles,
dropdown carets, table sort arrows. Stroke 2, 24×24 (or 16×16 inline),
`currentColor`. Anything brand-facing — marketing, onboarding, empty states,
section headers, feature cards, person-card badges — uses the 3D language.

Conventions (apply to both):

- **One icon per interactive surface.** Buttons may pair an icon + label, but
  the label always carries the meaning; icons are wayfinding only.
- **No icon-only buttons** without an accessible name (`aria-label`).
- **No emoji** anywhere. Status indicators use dot + label, never `✅` / `⚠️`.
- **No unicode symbols as icons.** The single exception is `♂ / ♀ / ⚧` for
  sex on person cards (matches the product's `pedigree-tree.tsx`).
- **Logo treatment:** the mark is the only "ornamental" SVG that does not
  follow the 3D-modern recipe. It uses Brand Blue + Deep Purple, never lilac
  or semantic colors.

## Substitutions flagged for review

- **Fonts:** PT Serif is vendored locally in `/fonts` as `.ttf`. Inter and
  JetBrains Mono are still loaded from Google Fonts CDN via the `@import`
  in `colors_and_type.css` — drop brand-licensed `.woff2` copies into
  `/fonts` and add matching `@font-face` blocks if offline / corporate-CDN
  delivery is required.
- **Logo:** the three uploaded PNG concepts contain glow / bevel / sparkle
  effects that the brief explicitly rejects. The production mark in
  `assets/logo/` is a flat vector redraw of the *idea* (rooted tree with
  ascending DNA helix and haplogroup ribbon) using the locked palette. Treat
  it as v1 and iterate.
