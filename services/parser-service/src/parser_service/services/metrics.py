"""Prometheus collectors для parser-service (Phase 9.0).

Минимальный observability stack — только counter'ы / гистограммы, без
Grafana / alertmanager / tracing (см. brief Phase 9.0). Экспонируются
через ``GET /metrics`` (см. ``parser_service.api.metrics``) в стандартном
Prometheus exposition format.

Все collectors живут в default registry ``prometheus_client.REGISTRY``,
чтобы не пришлось городить multiprocess-режим: parser-service — single
FastAPI process. Если в будущем перейдём на gunicorn workers — потребуется
``prometheus_client.multiprocess`` (отдельный ADR).

Расположение в ``services/`` (а не ``api/``) — collectors используются и
из API-handler'ов, и из background-сервисов (hypothesis_runner,
import_runner, …). API-роут только генерирует exposition для scrape.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ---- Hypothesis pipeline ----------------------------------------------------

hypothesis_created_total = Counter(
    "treegen_hypothesis_created_total",
    "Total hypotheses created (one increment per persisted Hypothesis row).",
    labelnames=("rule_id", "tree_id"),
)

hypothesis_review_action_total = Counter(
    "treegen_hypothesis_review_action_total",
    "Hypothesis review actions (PATCH /hypotheses/{id}/review).",
    labelnames=("action",),  # confirmed/rejected/pending
)

# Compute jobs.
hypothesis_compute_duration_seconds = Histogram(
    "treegen_hypothesis_compute_duration_seconds",
    "Time to run a single hypothesis composition (compose_hypothesis call).",
    labelnames=("rule_id",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0, 120.0),
)

# ---- Imports ----------------------------------------------------------------

import_completed_total = Counter(
    "treegen_import_completed_total",
    "Imports completed (GEDCOM and FamilySearch).",
    labelnames=("source", "outcome"),  # gedcom|fs ; success|error
)

# ---- Dedup ------------------------------------------------------------------

dedup_finder_duration_seconds = Histogram(
    "treegen_dedup_finder_duration_seconds",
    "Dedup scorer execution time (per find_*_duplicates call).",
    labelnames=("entity_type",),  # person|source|place
    buckets=(0.01, 0.1, 0.5, 1.0, 5.0, 30.0),
)


__all__ = [
    "dedup_finder_duration_seconds",
    "hypothesis_compute_duration_seconds",
    "hypothesis_created_total",
    "hypothesis_review_action_total",
    "import_completed_total",
]
