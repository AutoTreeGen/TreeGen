output "notification_channel_id" {
  description = "Resource id of the email notification channel — useful for binding additional ad-hoc alerts."
  value       = google_monitoring_notification_channel.email.id
}

output "log_metric_parser_import_failed" {
  description = "Name of the log-based metric counting parser import failures."
  value       = google_logging_metric.parser_import_failed.name
}

output "alert_policy_ids" {
  description = "Map of alert policy short name → resource id."
  value = {
    cloud_run_5xx         = google_monitoring_alert_policy.cloud_run_5xx.id
    alloydb_cpu_high      = google_monitoring_alert_policy.alloydb_cpu_high.id
    cloud_run_memory_high = google_monitoring_alert_policy.cloud_run_memory_high.id
  }
}
