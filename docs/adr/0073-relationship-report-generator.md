# ADR 0073 — Relationship Research Report Generator (Phase 24.3)

* Status: Accepted
* Date: 2026-05-02
* Phase: 24.3
* Supersedes-note: extends Phase 15.6 court-ready PDF stack (#180); see
  "Why a separate service" below.

## Context

Phase 24.3 adds a per-relationship research-grade PDF deliverable. Genealogists
charge clients $500–2000 per case and currently hand-write the report in Word;
emitting a structured PDF directly from the tree + evidence model is the
pro-revenue path. The report contains:

* claim (which two people, what relationship is asserted),
* supporting evidence (citations, off-catalog 22.5 Evidence rows, hypothesis
  evidence, optional DNA matches),
* counter-evidence (hypothesis evidence with `direction=contradicts`),
* composite confidence (Phase 22.5 weighted aggregation),
* sources / Chicago footnotes,
* methodology + signature page.

Phase 15.6 (#180) already shipped a Jinja2 + WeasyPrint PDF stack inside
`parser-service`, with explicit Chicago citation formatter, locale module,
ObjectStorage backend, and signed-URL TTL. That commit also documented the
service-placement decision:

> Supersedes-note (no new ADR, per agreed scope):
> ADR-0058 §"Endpoint лежит в parser-service" applies to reports too —
> report endpoints live in parser-service rather than a new report-service.
> If export volume grows (multi-tree, async batch), revisit by carving out
> a separate service in a future phase.

## Decision

Phase 24.3 instantiates the "carve out a separate service" branch of that
clause. We add `services/report-service/` as a new uv workspace member,
hosting the new `POST /api/v1/reports/relationship` endpoint plus its
narrative / confidence / data / locale / render modules and Jinja templates.

Rationale for the carve-out (vs. re-using `parser-service` directly):

1. **Independent deploy unit for the pro-revenue surface.** Genealogist-
   facing PDF generation has a different load profile (bursty, customer-
   visible deadlines, larger memory per request) than parser-service's
   tree-edit hot path. Sharing a process means one OOM kills both.
2. **Smaller blast-radius for a paid feature.** PDF rendering is the
   pro-tier monetisation path; isolating it lets us scale, gate, and
   meter it independently of free-tier tree-edit traffic.
3. **Future report types (24.4 chained-evidence, 24.5 court-ready audit
   delta) live in the same place.** 24.x cluster is explicitly a multi-
   report family in the roadmap; the natural home is a dedicated
   service rather than a third tab inside parser-service.

Cost we accept:

* The Chicago citation formatter and Jinja-render helper from 15.6 are
  re-implemented inside report-service rather than imported. This is
  ~150 LOC of duplication, but it keeps report-service free of any
  runtime dependency on parser-service (which would otherwise drag in
  ai-layer, FamilySearch client, Stripe SDK, etc. — all unrelated).
  When 24.4 adds another report type and the duplication grows to ~400
  LOC, extract `packages/pdf-rendering/` per the 15.6 supersedes-note's
  "carve out" pattern at the package level.

## Data model

No new ORM tables. Read-only against existing 15.x evidence + 22.5
`Evidence` + `Hypothesis` + `Family` / `FamilyChild` / `Citation` /
`Source` / `DnaMatch`. Confidence formula uses Phase 22.5 semantics:
`Σ supporting weight × match_certainty − Σ contradicting weight ×
match_certainty`, clamped to ≥ 0.

## Endpoints

* `POST /api/v1/reports/relationship` — sync generation, returns
  `{report_id, pdf_url, expires_at, confidence, evidence_count,
  counter_evidence_count}`.
* `GET /healthz` — liveness probe.

Auth: `X-User-Id` header (mirrors billing-service pre-Phase-4.10
pattern). Upstream API gateway validates the Clerk JWT and injects the
header. Permission gate: VIEWER+ on `tree_id` via `TreeMembership` (with
the same `trees.owner_user_id` fallback as Phase 11.0).

PDF storage: `ObjectStorage` via `shared_models.storage.build_storage_from_env`
— same as 15.6. Key shape: `relationship-reports/{tree_id}/{report_id}.pdf`.
Signed-URL TTL: configurable, default 24h.

## Scope of v1

* **Direct relationships** (`parent_child`, `sibling`, `spouse`) get full
  evidence aggregation — Family/FamilyChild resolver + Citation +
  Hypothesis + Off-catalog Evidence + (optional) DNA matches.
* **Extended distances** (cousins, grandparent, aunt/uncle) accept the
  claim and render the report, but do not chain evidence through
  intermediate generations — narrative includes an explicit caveat.
  Phase 24.4 will add chained-evidence aggregation.
* DNA evidence is best-effort: any `DnaMatch` row whose
  `matched_person_id` is one of the pair is included with
  `weight = min(1.0, total_cm / 200)` and `match_certainty = 0.8`.
  Kit-owner verification (proper "A's kit matched B" semantics) is
  Phase 24.4.
* **No subscription / billing logic.** Pro-tier gating is a separate
  phase — Phase 24.3 is the generation engine, not the paywall.

## Tests

* Unit (`test_relationship_render_unit.py`): narrative determinism,
  confidence formula coverage (pure-supporting / mixed / clamped /
  asserted-only / 22.5 weighting), counter-evidence section conditional
  rendering, footnote dedup, extended-claim caveat, ru-locale roundtrip.
* Integration (`test_relationship_api.py`): testcontainers-postgres +
  alembic-upgrade-head, seeded `User + Tree + TreeMembership +
  2 Person + 2 Name + Family + FamilyChild + Citation + Source`, full
  POST /api/v1/reports/relationship → 200 with evidence_count ≥ 1, plus
  401 / 400 / 404 cases. PDF byte-length asserted only when WeasyPrint
  native libs are present; otherwise the integration test skips with
  the same 503 the endpoint returns to clients on a bare Windows host.

## Future work

* Phase 24.4 — chained evidence for cousin / grandparent claims; proper
  DNA kit-owner resolution.
* Phase 24.5 — extract `packages/pdf-rendering/` once the duplication
  with `parser-service/court_ready/` exceeds ~400 LOC.
* Phase 24.6 — async PDF job (mirror Phase 4.11a/b GDPR exporter) once
  any single relationship report exceeds ~5s render time.
