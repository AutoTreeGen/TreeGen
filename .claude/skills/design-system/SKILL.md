---
name: AutoTreeGen Design System
description: |
  Visual + interaction language for AutoTreeGen — an evidence-based genealogy
  platform unifying GEDCOM trees, DNA matches, and historical archives. Use
  this system when designing any AutoTreeGen surface: marketing pages, the web
  app (tree explorer, person card, hypothesis panel, source citations, DNA
  match list), or internal tooling. The system is purple-and-archive: deep
  purple surfaces, brand blue for action, ink for text, snow for canvas;
  PT Serif display (vendored ttf), Inter body, JetBrains Mono for DNA codes; restrained
  shadows, no gradients, no emoji, sentence case throughout. Voice is a
  careful researcher's lab notebook — every claim cited, every confidence
  scored, no marketing fluff.
when_to_use: |
  Any time the user is designing for AutoTreeGen or asks for an
  evidence-based / archival / genealogical aesthetic with strict provenance.
  Prefer this system over generic "data app" or "consumer DNA" patterns.
---

# How to use this design system

1. **Read `README.md` first.** It carries the full content rules, visual
   foundations, voice examples, and anti-patterns. Most design questions are
   answered there before you touch a token.
2. **Link `colors_and_type.css`** in any HTML you produce — it is the single
   source of truth for color, type, spacing, radii, shadow, and motion
   tokens. Do not redefine tokens locally.
3. **Browse `preview/`** to see every token group and component cluster as a
   card. Each preview is registered for the Design System tab.
4. **Compose from `ui_kits/`:**
   - `ui_kits/marketing/` — landing page (hero, features, process, pricing,
     waitlist).
   - `ui_kits/web-app/` — product app (sidebar, top bar, tree canvas,
     inspector with Facts / Hypotheses / DNA / Notes tabs).
5. **Use the locked palette as-is.** Deep Purple for surfaces, Brand Blue for
   action, Ink for text, Lilac as a tint only. Haplogroup colors are for tree
   visualisations only — never UI chrome.
6. **Voice rules are not optional.** Sentence case everywhere. No emoji. No
   marketing fluff. Every fact paired with a citation or confidence.
7. **When in doubt, choose less.** This system errs toward restraint — paper,
   not film; museum collection software, not consumer dashboard.

## Quick reference

- **Fonts:** PT Serif (display, headings, archival voice) · Inter (body /
  UI) · JetBrains Mono (DNA, GEDCOM tags, IDs). PT Serif is vendored
  locally in `/fonts`.
- **Brand colors:** `#2C1054` Deep Purple · `#2E5DA5` Brand Blue · `#1A1024`
  Ink · `#B070C0` Lilac (tint only) · `#E8D5F0` Light Lilac · `#FFFFFF`
  Snow.
- **Haplogroups:** R1a `#2E5DA5` · J2 `#C04B7E` · H1a `#4BB39E` ·
  T1 `#C0904B` · E1b `#7E4BC0` · I1 `#4BC07E` · G2 `#C04B4B` ·
  N1c `#4B7EC0`. Always paired with a label and a shape pattern.
- **Spacing:** 4-based (`2 4 8 12 16 20 24 32 40 48 64 80 96 128`).
- **Radii:** sm 4 · md 6 · lg 10 · xl 16 · full.
- **Shadows:** sm at rest · md on hover/sticky · lg on modals only.
- **Motion:** house curve `cubic-bezier(0.2, 0.8, 0.2, 1)`; durations 120 /
  200 / 320 ms; fades dominate.
- **Iconography — 3D modern.** Brand icons are chunky soft-body 3D objects
  rendered as inline SVG, sitting on a tinted radial-gradient backdrop (1:1
  rounded card, six tints rotated). Each glyph uses the `b3*` palette of
  multi-stop radial gradients (key-light at 0.32, 0.22), a white specular
  ellipse (`url(#b3Spec)`), an optional cool ambient bounce
  (`url(#b3Ambient)`, screen blend), `url(#b3Hole)` for openings, a soft
  cast shadow on the ground plane, and the `b3Shadow` filter for contact
  shadow. **No medallion / coin frame, no flat fills, no outlines on
  bodies, no helix-chromatic palette.** See
  `preview/brand-icon-style-spec.html` for the recipe and
  `preview/brand-iconography.html` for the canonical 24-icon set. Lucide is
  still fine for tiny inline UI affordances inside the product (chevrons,
  close, drag handles), but anything brand-facing uses the 3D language.

## Anti-patterns (do not do)

- No gradients as fills (haplogroup ribbon viz is the single exception).
- No emoji. Status uses dot + label.
- No glow, glassmorphism, drop-shadow-as-emphasis, sepia, celtic motifs.
- No "Discover your story", no exclamation marks, no "amazing / powerful /
  unlock".
- No icon-only buttons without `aria-label`.
- No facts without a source — every name, date, place links to a citation.
