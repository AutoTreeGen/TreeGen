// =============================================================================
// Phase 10.9a — voice-to-tree audio sessions bucket (staging-only).
//
// Owner-recorded audio is uploaded here before Whisper transcription. The
// bucket is provisioned in staging only because:
//   - prod rollout is gated on the demo MVP (06.05.2026, ADR-0064);
//   - per-tree consent (`tree_settings.consent_egress_at`) is enforced at
//     the API layer, not in object storage — egress without consent is a
//     critical privacy incident (see runbook).
//
// Lifecycle: hard delete after 365 days. Audio is the source-of-truth for
// transcripts only until review; once committed to the tree, the audio is
// no longer needed. The 365d ceiling exists for re-review of contested
// transcriptions, not for archival.
//
// Labels surface this bucket as PII in cost / IAM dashboards and link it to
// the originating phase for sunset/cleanup later.
// =============================================================================

resource "google_storage_bucket" "audio_sessions" {
  name                        = "${var.bucket_prefix}-${var.name}-audio-sessions"
  location                    = var.region
  force_destroy               = true // staging is ephemeral — same as module "storage"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  // Аудио не versionируется — round-trip из транскрипта в исходник нам не нужен,
  // а версии хранения PII только увеличивают surface на erasure-запросы.
  versioning {
    enabled = false
  }

  // 365 дней — TTL retention. Координируется с GDPR-policy: дольше не нужно,
  // короче — рискуем потерять источник для оспоренного транскрипта.
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 365
    }
  }

  labels = {
    phase   = "10-9a"
    purpose = "voice-to-tree-audio"
    pii     = "yes"
  }
}

// =============================================================================
// Phase 10.9a — privacy alert: audio egress without consent.
//
// Triggers when parser-service emits a log entry with
// `event=audio_egress_attempt consent_present=false`. По спеке (ADR-0064 §B
// + spec §3.5) фронт visually disable'нет Record до consent, а backend
// дублирует проверку — defence-in-depth. Эта метрика существует, чтобы
// поймать нарушение инварианта (фронт скомпрометирован / regress в backend
// валидаторе).
//
// SLO: > 0 за 5 минут — paging-incident. См. runbook
// `docs/runbooks/voice-to-tree.md#privacy-incident-response` (GDPR Art. 33,
// 72-часовой clock).
// =============================================================================

resource "google_logging_metric" "audio_egress_without_consent" {
  name        = "${var.name}/audio_egress_without_consent"
  description = "Count of audio egress attempts without `consent_egress_at` (privacy invariant)"
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${var.name}-parser-service"
    severity>=WARNING
    jsonPayload.event="audio_egress_attempt"
    jsonPayload.consent_present="false"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "tree_id"
      value_type  = "STRING"
      description = "Tree whose consent invariant was violated"
    }
    display_name = "Audio egress without consent (${var.name})"
  }

  label_extractors = {
    "tree_id" = "EXTRACT(jsonPayload.tree_id)"
  }
}

resource "google_monitoring_alert_policy" "audio_egress_without_consent" {
  display_name = "Audio egress without consent (${var.name})"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "Any audio egress without consent in 5 min"
    condition_threshold {
      filter = <<-EOT
        metric.type="logging.googleapis.com/user/${google_logging_metric.audio_egress_without_consent.name}"
        resource.type="cloud_run_revision"
      EOT
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_DELTA"
      }
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s" // immediate page on first occurrence
      trigger {
        count = 1
      }
    }
  }

  notification_channels = [module.monitoring.notification_channel_id]

  documentation {
    content   = "**PRIVACY INCIDENT — GDPR Art. 33, 72h clock starts.** parser-service logged `audio_egress_attempt` with `consent_present=false`. Follow `docs/runbooks/voice-to-tree.md#privacy-incident-response`: 1) feature-flag off, 2) audit logs by tree_id, 3) notify affected user within 24h, 4) postmortem."
    mime_type = "text/markdown"
  }

  user_labels = {
    phase    = "10-9a"
    category = "privacy"
  }
}
