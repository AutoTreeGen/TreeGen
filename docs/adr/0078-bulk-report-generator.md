# ADR 0078 — Bulk Relationship Report Generator (Phase 24.4)

* Status: Accepted
* Date: 2026-05-02
* Phase: 24.4
* Extends: ADR-0073 (Phase 24.3 single-relationship PDF service)

## Context

Phase 24.3 (PR #194) shipped a synchronous per-relationship PDF endpoint
inside `services/report-service/`. Pro genealogists running site-survey
reports (e.g. all 50+ candidate cousin relationships in a tree) currently
have to fire 50 sequential POST requests, each blocking ~1–3s on
WeasyPrint render and storage upload. They want one click → eventual
download of either a ZIP-of-PDFs or a single consolidated PDF.

Constraints (per brief):

* 24.3 sync API stays unchanged (additive only).
* No new job runner — reuse existing `arq` (the only async runner in the
  monorepo, currently used by `parser-service`).
* Don't fork the 24.3 per-pair PDF logic — bundle worker must call the
  same function, not a duplicate.
* No PII in storage paths or filenames (job_id only, never names).
* No frontend in this PR.

## Decision

Add four endpoints + one ORM table + one `arq` worker process inside the
existing `services/report-service/`:

* `POST   /api/v1/trees/{tree_id}/report-bundles` → 202 with `job_id`
* `GET    /api/v1/trees/{tree_id}/report-bundles/{job_id}` → status snapshot
* `GET    /api/v1/trees/{tree_id}/report-bundles/{job_id}/download` → blob
* `DELETE /api/v1/trees/{tree_id}/report-bundles/{job_id}` → 204

New table: `report_bundle_jobs` (alembic 0042) with status lifecycle
`queued → running → {completed | failed | cancelled}`, atomic counter
columns `completed_count` / `failed_count`, JSONB
`relationship_pairs` input + `error_summary` output, 7-day TTL with
hourly cron-driven purge.

### Worker placement: arq inside report-service

The brief said "use existing arq". Two readings:

* (a) plug report-service into `parser-service`'s arq worker process —
  forces parser-service to import report-service code, reverses the
  no-runtime-cross-dep choice from ADR-0073;
* (b) stand up a **second arq deployment** inside report-service, sharing
  the same Redis instance via `REDIS_URL` but listening on its own queue
  `report-bundles` — same library, no new framework, isolated process.

Chose (b). arq is the runner; both deployments use it. The "no new job
runner" constraint is about not introducing a different async stack
(celery, dramatiq, RQ), not about restricting where arq is hosted.
Cross-service Python imports remain forbidden.

### Queue isolation

`report-bundles` queue is separate from parser-service's `imports`
queue so PDF render workloads don't compete with GEDCOM import /
inference for arq concurrency slots.

### Anti-fork: single source of truth for per-pair PDF

24.3 endpoint body was inlined: `build_report_context → render_html →
render_pdf → storage.put → response`. 24.4 extracts the first three
steps into `report_service.relationship.pipeline.generate_pdf_bytes_for_pair`.
The 24.3 endpoint refactors to call this function (no public API change —
same body, same response shape, same error mapping). The bundle worker
calls the same function. Storage / signed-URL handling stays in the
respective call-sites because layouts differ:

* sync endpoint: `relationship-reports/{tree_id}/{report_id}.pdf`
* bundle: `relationship-bundles/{tree_id}/{job_id}.{zip|pdf}`

A spy test (`test_reuse_24_3_single_report_function`) monkeypatches the
function at both the canonical module and the runner's import site; if
either is forked, the assertion fails.

### Auto-derive claim

`relationship_pairs` items have an optional `claimed_relationship`. NULL
triggers `auto_derive_claim`: run the three direct-claim resolvers
(parent_child / sibling / spouse); pick the highest-priority match
(`parent_child > spouse > sibling`). If none match, fail the pair with
"specify claimed_relationship explicitly" — extended distances (cousin,
grandparent, aunt/uncle) need chained-evidence aggregation that 24.3
explicitly defers (see ADR-0073 §"Scope of v1"); auto-derive doesn't
attempt them.

### Output formats

* `zip_of_pdfs` (default): per-pair PDFs as `{pair_index:04d}.pdf` plus
  `manifest.json` mapping index → metadata. **PII-safe filenames** —
  numeric index, never names.
* `consolidated_pdf`: single WeasyPrint render of cover + TOC +
  per-pair sections via `page-break-before: always`. Falls back to
  `zip_of_pdfs` if WeasyPrint native libs are missing.

### TTL + purge

7-day TTL by default. `ttl_expires_at = created_at + 7d`. Hourly arq
cron job runs `purge_expired_bundles`: SELECT WHERE
`ttl_expires_at < now`, delete blob from storage (best-effort, log on
failure), DELETE row. Storage delete failures don't block DB cleanup —
otherwise an orphan blob holds up the queue.

### Permission gate

Same as 24.3: `X-User-Id` header + VIEWER+ on `tree_id` via
`TreeMembership` with `trees.owner_user_id` fallback (mirrors
Phase 11.0). Stranger gets 404 (not 403) — no info leak about tree
existence.

### Status code map

* 202 — POST: job created and (best-effort) enqueued. arq enqueue failure
  is logged but does NOT 5xx — TTL cron eventually purges abandoned
  rows; user can retry.
* 409 — download requested before completion.
* 410 — download requested after `ttl_expires_at`.
* 404 — bundle not in this tree (or stranger access).
* 422 — would be raised by API for unsupported claim types if we
  rejected them eagerly; currently 24.4 v1 fails such pairs at the
  worker level and surfaces them in `error_summary`.

## Alternatives rejected

1. **Make 24.3 itself async.** Breaks every existing sync caller of POST
   /api/v1/reports/relationship; immediate cost for hypothetical bulk
   future. Sync stays for ad-hoc single-pair use.
2. **Client-side parallel calls.** No queueing, no progress, no
   consolidation, hits LLM/PDF rate limits unpredictably, no retry
   coordination.
3. **Introduce a new job runner (celery / dramatiq).** Adds infra,
   breaks "no new job runner" constraint, no benefit over second arq
   deployment for current scale.

## Consequences

* Schema: new service-table `report_bundle_jobs` requires
  `SERVICE_TABLES` allowlist update in `test_schema_invariants.py`
  (per `feedback_orm_allowlist` memory rule).
* Storage cost: per-bundle blobs accumulate until TTL. 7-day default
  with hourly purge keeps total bounded; explicit per-job DELETE for
  immediate cleanup.
* Operability: report-service now has TWO process types (web + arq
  worker) — deploy must run both. CI doesn't currently spin a worker;
  integration tests invoke `run_bundle_job` directly (no arq).
* Confidence threshold: API exposes `confidence_threshold` as a soft
  filter — pairs below the threshold count as `failed` (in
  `error_summary`) rather than excluded silently. Honest bundle surface.

## Future work

* Phase 24.4.1 — frontend bulk-select UI (post-Geoffrey-demo ticket).
* Phase 24.5 — extract `packages/pdf-rendering/` once ADR-0073's
  duplication threshold (~400 LOC between report-service and
  parser-service court_ready) is met; 24.4 doesn't add to that count
  because it imports from 24.3's pipeline rather than duplicating.
* Phase 24.6 — Cloud Tasks fan-out for prod scale (mirror parser-service
  `queue.py` BACKEND_CLOUD_TASKS branch) when single-Redis throughput
  becomes a bottleneck.
* Cousin / grandparent auto-derive — needs chained evidence aggregation
  from Phase 24.5+ chained-claim work.
