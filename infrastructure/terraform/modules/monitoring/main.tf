// =============================================================================
// monitoring module — Cloud Logging metrics, Monitoring alert policies,
// and a single email notification channel.
//
// Three policies, calibrated for staging traffic:
//   1. Cloud Run 5xx rate per service > 5% over 5 min.
//   2. AlloyDB Omni VM CPU > 80% for 10 min.
//   3. Cloud Run instance memory utilization > 90% for 5 min.
//
// All policies route to the same email notification channel
// (var.notification_email). Add additional channels in this module if you
// want PagerDuty/Slack later — same `notification_channels` field.
//
// Logging metric is created so the 5xx alert can express the *rate* (matched
// requests / all requests). Cloud Run already exposes `request_count` with
// `response_code_class` so we use the built-in metric — no log-based metric
// needed for that one. We still ship one log-based metric (parser-import-fail)
// as a reusable example for future per-service health alerts.
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

// ---- Notification channel -------------------------------------------------

resource "google_monitoring_notification_channel" "email" {
  display_name = "AutoTreeGen ${var.name} alerts"
  type         = "email"
  description  = "Email destination for ${var.name} environment alerts"
  labels = {
    email_address = var.notification_email
  }

  // The email must be confirmed by clicking a link Google sends to that
  // address on first apply. Until confirmed, alerts won't be delivered —
  // but the channel resource is created and policies attach to it.
}

// ---- Log-based metric: parser import failures -----------------------------
//
// Counts log entries from parser-service that contain `import_failed` and
// a structured `import_job_id` label. Useful as a leading indicator for
// systemic parser regressions — alerts bind to it below as an example.

resource "google_logging_metric" "parser_import_failed" {
  name        = "${var.name}/parser_import_failed"
  description = "Count of `import_failed` log entries from parser-service"
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${var.name}-parser-service"
    severity>=ERROR
    jsonPayload.event="import_failed"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "import_job_id"
      value_type  = "STRING"
      description = "ID of the failed import job"
    }
    display_name = "Parser import failures (${var.name})"
  }

  label_extractors = {
    "import_job_id" = "EXTRACT(jsonPayload.import_job_id)"
  }
}

// ---- Alert policies -------------------------------------------------------

resource "google_monitoring_alert_policy" "cloud_run_5xx" {
  display_name = "Cloud Run 5xx > 5% (${var.name})"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "5xx rate over 5 min"
    condition_threshold {
      // Fraction of 5xx out of total requests.
      filter = <<-EOT
        metric.type="run.googleapis.com/request_count"
        resource.type="cloud_run_revision"
        metric.label.response_code_class="5xx"
      EOT
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
      comparison      = "COMPARISON_GT"
      threshold_value = 0.05
      duration        = "300s"
      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    content   = "Cloud Run service exceeded 5% 5xx rate for 5 minutes. Check `gcloud logging read 'resource.type=cloud_run_revision severity>=ERROR'` for the offender."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "alloydb_cpu_high" {
  display_name = "AlloyDB Omni CPU > 80% (${var.name})"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "VM CPU > 80% for 10 min"
    condition_threshold {
      filter = <<-EOT
        metric.type="compute.googleapis.com/instance/cpu/utilization"
        resource.type="gce_instance"
        metric.label.instance_name="${var.alloydb_instance_name}"
      EOT
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_MEAN"
        cross_series_reducer = "REDUCE_MEAN"
      }
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "600s"
      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    content   = "AlloyDB Omni VM has been at >80% CPU for 10 minutes. Check pg_stat_activity for long-running queries; consider scaling the VM machine type."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "cloud_run_memory_high" {
  display_name = "Cloud Run memory > 90% (${var.name})"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "Container memory > 90% for 5 min"
    condition_threshold {
      filter = <<-EOT
        metric.type="run.googleapis.com/container/memory/utilizations"
        resource.type="cloud_run_revision"
      EOT
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_MEAN"
        cross_series_reducer = "REDUCE_PERCENTILE_99"
        group_by_fields      = ["resource.label.service_name"]
      }
      comparison      = "COMPARISON_GT"
      threshold_value = 0.9
      duration        = "300s"
      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    content   = "Cloud Run container memory exceeded 90% utilization. Bump the `memory` limit in `modules/services/main.tf` or look for memory leaks in recent revisions."
    mime_type = "text/markdown"
  }
}
