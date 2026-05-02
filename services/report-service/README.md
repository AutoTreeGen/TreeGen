# report-service

Phase 24.3 — Research Report Generator. Stand-alone service that produces
per-relationship research-grade PDFs (claim + evidence + counter-evidence +
confidence + sources).

Carved out from `parser-service` per Phase 15.6 supersedes-note's "if export
volume grows, revisit by carving out a separate service" clause. Pro-revenue
path: genealogists charge clients $500–2000 per case and currently hand-write
the deliverable in Word; this endpoint emits a defensible PDF directly from
the tree + evidence model.

## Endpoints

* `POST /api/v1/reports/relationship` — generate a per-relationship PDF.
  Body:

  ```json
  {
    "tree_id": "<uuid>",
    "person_a_id": "<uuid>",
    "person_b_id": "<uuid>",
    "claimed_relationship": "parent_child",
    "options": {
      "include_dna_evidence": true,
      "include_archive_evidence": true,
      "include_hypothesis_flags": true,
      "locale": "en",
      "title_style": "formal"
    }
  }
  ```

  Returns `{report_id, pdf_url, expires_at, confidence, evidence_count, counter_evidence_count}`.

* `GET /healthz` — liveness probe.

## Local run

```sh
uv run uvicorn report_service.main:app --reload --port 8006
```

## Tests

```sh
uv run pytest services/report-service
```

Integration tests need testcontainers + a Linux (or WeasyPrint-capable
Windows) host. PDF byte-length asserts skip with a 503 if WeasyPrint native
libs are missing.

## Design notes

* Reuses Phase 15.6 layout vocabulary (Chicago footnotes, A4 page styling,
  signature block, footnote dedup) but ships its own Jinja env so the new
  service has no runtime dependency on `parser-service`.
* Read-only against existing 15.x evidence + 22.5 `Evidence` rows. No
  Alembic migration in this PR.
* No subscription / billing logic — Pro-tier gating is a separate phase.
