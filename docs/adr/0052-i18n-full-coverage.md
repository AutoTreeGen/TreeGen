# ADR-0052 — i18n full coverage across authenticated app + public pages

**Status:** accepted
**Date:** 2026-04-30
**Phase:** 4.13b

## Context

Phase 4.13a (#121) shipped the i18n foundation — `next-intl` config, locale
routing, the linter at `scripts/check_i18n_strings.py`, the parity test in
`locale-rendering.test.tsx`, and migration of the smallest authenticated pages
(dashboard, settings/notifications) plus marketing surfaces. Larger pages were
deferred: the linter only runs on staged files, so the existing English text
on `trees/[id]/*`, `persons/[id]/*`, `dna/*`, `hypotheses/*`, `sources/*`,
`familysearch/*` was grandfathered until the next time someone touched each
file.

That worked for a release but it leaks: any unrelated PR that touches one of
those pages suddenly trips the linter and needs to do incidental i18n work
mid-feature (we hit this in the Phase 11.2 PR — three pre-existing strings on
the access page had to be wrapped just because we added a tab there).

This phase closes the gap.

## Decision

Wrap **every JSX text node the linter currently flags** across the
authenticated app and the recently-added Phase 11.2 public read-only page.
That means: every `>Capitalized text<` single-line node in
`apps/web/src/app/{trees,persons,dna,hypotheses,sources,familysearch}/**.tsx`
goes through `useTranslations("<namespace>")` with the matching key in
`apps/web/messages/{en,ru}.json`.

Coverage of the file `public/[token]/page.tsx` is intentionally deferred —
the file lives on an open PR (#138, Phase 11.2). Stacking this work on an
unmerged branch was rejected (see CLAUDE.md and prior incident with cascading
`gh pr update-branch` rebases — owner explicitly endorsed waiting for upstream
to merge). A follow-up micro-PR (4.13c) will cover that one file.

## Namespace layout

We extend the existing nested-namespace convention (already established by
`trees.stats` from #137 and `persons.merge` from #131):

* `trees.access`, `trees.duplicates`, `trees.hypotheses`, `trees.import`,
  `trees.persons` — per-route under `/trees/[id]/`.
* `persons.detail`, `persons.tree`, `persons.mergeRoute` — per-route under
  `/persons/[id]/`. Note `persons.merge` is already taken by the Phase 6.4
  manual-merge UI; `persons.mergeRoute` is the route-level shell page.
* `dna.list`, `dna.kitMatches`, `dna.matchDetail` — `/dna/`,
  `/dna/[kitId]/matches/`, `/dna/matches/[matchId]/`.
* `hypotheses.detail` — `/hypotheses/[id]/`.
* `sources.detail` — `/sources/[id]/`.
* `familysearch.connect`, `familysearch.importStatus`, `familysearch.preview` —
  three route-shells.

Russian translations are first-class native — no machine translation, no
en-passthrough.

## Scope is the linter, not "every English string"

The linter regex is `>\s*([A-Z][A-Za-z][A-Za-z .,!?'’—\-]{3,})\s*<` and only
catches single-line capitalized JSX text. It deliberately misses:

* Multi-line `<p>...\n...</p>` blocks.
* Attribute strings (`placeholder=`, `aria-label=`, `title=`).
* `confirm()` / `alert()` text in JS logic.
* Toast / notification strings rendered via JS APIs.

We accept this gap. The linter's job is to make i18n drift surface in PR diffs;
it is not a hard guarantee of "100% of English strings are localized". Adding
attribute and runtime-message coverage costs more than it earns at this point
(higher false-positive rate, more friction on every PR). The user-visible
hardcoded English that does land in HTML is now i18n'd; the long tail will be
picked up opportunistically when each file is next touched, same model as
4.13a.

## Russian-translation completeness

To prevent "ru.json copy-pasted from en.json" regressions, this phase adds
`locale-rollout-4-13b.test.ts` which asserts:

1. Each new namespace exists in both locales.
2. No value in any new namespace is empty.
3. No more than 5% of new keys have identical en/ru values (allows for proper
   nouns like `FamilySearch` and unit labels like `Min cM`).

This complements the Phase 4.13a parity test (key-set equality) — parity tells
us "no key is missing", the new test tells us "no key was forgotten to
translate".

## What this phase does NOT do

* Does not re-i18n anything Phase 4.13a already covered (dashboard, settings,
  notifications, marketing pages, ErrorMessage component).
* Does not add a runtime missing-key surfacing mechanism (e.g. throwing in
  dev, sending Sentry events in prod). `next-intl` already logs warnings;
  upgrading to throw-on-missing in dev mode is a separate decision.
* Does not localize the `public/[token]/page.tsx` page from #138 — covered by
  a follow-up 4.13c PR after #138 merges.
* Does not localize internal admin pages (none exist yet) or developer-only
  surfaces (debug routes, etc.).

## Consequences

* CI pre-commit hook for i18n now passes on the full authenticated app
  surface. Future PRs that touch those files inherit a clean baseline — no
  more "you touched a file, fix three pre-existing English strings"
  surprises.
* `apps/web/messages/{en,ru}.json` grows by 29 keys (one per linter
  violation across 14 files). Both locales remain at full key-parity.
* Translation maintenance cost per new feature is now constant — every new
  user-visible string requires en + ru entries up front, caught by linter
  * parity test. There is no "we'll i18n it later" loophole left.

## Alternatives considered

* **Run linter as a baseline + only fail on regressions.** Rejected: complex
  to maintain (baseline file rots), and we already crossed the work threshold
  by touching most files in unrelated PRs. Hard cutover is cheaper.
* **Auto-extract strings via codegen / babel plugin.** Rejected: 29 strings
  is small enough to handle by hand, codegen adds toolchain complexity for
  marginal benefit.
* **Localize attribute strings (placeholders, aria-labels) in the same PR.**
  Deferred: 3–4× more strings, lower visibility ROI, no linter to enforce.
  Picked up opportunistically.
