// =============================================================================
// queue module — Cloud Tasks queues for asynchronous parser/notification work.
//
// Queues mirror the local arq queue names so application code can switch
// backends via PARSER_SERVICE_QUEUE_BACKEND={arq|cloud_tasks} without
// renaming jobs:
//   - imports         — GED + FamilySearch import jobs (Phase 3.5 / 5.1).
//   - hypotheses      — bulk hypothesis compute (Phase 7.5).
//   - notifications   — email/webhook fanout (Phase 8).
//
// Retry policy is conservative — long-running parser jobs handle their own
// idempotency via job-id deduplication (see ADR-0028).
// =============================================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.40, < 7.0"
    }
  }
}

locals {
  queues = {
    imports = {
      max_attempts          = 3
      max_dispatches_per_s  = 10
      max_concurrent        = 5
      max_retry_age_seconds = 3600
    }
    hypotheses = {
      max_attempts          = 3
      max_dispatches_per_s  = 5
      max_concurrent        = 2
      max_retry_age_seconds = 7200
    }
    notifications = {
      max_attempts          = 5
      max_dispatches_per_s  = 50
      max_concurrent        = 20
      max_retry_age_seconds = 600
    }
  }
}

resource "google_cloud_tasks_queue" "queue" {
  for_each = local.queues

  name     = "${var.name}-${each.key}"
  location = var.region

  rate_limits {
    max_dispatches_per_second = each.value.max_dispatches_per_s
    max_concurrent_dispatches = each.value.max_concurrent
  }

  retry_config {
    max_attempts       = each.value.max_attempts
    max_retry_duration = "${each.value.max_retry_age_seconds}s"
    min_backoff        = "5s"
    max_backoff        = "60s"
    max_doublings      = 4
  }
}
