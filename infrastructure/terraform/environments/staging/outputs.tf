output "service_urls" {
  description = "Cloud Run service URLs (web is public; others reached internally)."
  value       = module.services.service_urls
}

output "service_accounts" {
  description = "Map of service short name → runtime SA email."
  value       = module.services.service_account_emails
}

output "database_host" {
  description = "Internal IP of the AlloyDB Omni VM."
  value       = module.database.private_ip
}

output "database_url_template" {
  description = "DSN template — services substitute the DB password from Secret Manager at runtime."
  value       = module.database.database_url
  sensitive   = true
}

output "buckets" {
  description = "Map of bucket purpose → bucket name."
  value = {
    ged_uploads    = module.storage.ged_uploads_bucket
    dna_data       = module.storage.dna_data_bucket
    multimedia     = module.storage.multimedia_bucket
    audio_sessions = google_storage_bucket.audio_sessions.name
  }
}

output "queues" {
  description = "Cloud Tasks queue resource IDs."
  value       = module.queue.queue_ids
}

output "secret_names" {
  description = "Map of logical secret name → Secret Manager short name. Set initial values via `gcloud secrets versions add`."
  value       = module.secrets.secret_short_names
}

output "gha_workload_identity_provider" {
  description = "Feed this to `google-github-actions/auth@v2` as `workload_identity_provider` (a GitHub repo variable)."
  value       = module.gha_oidc.workload_identity_provider
}

output "gha_deployer_service_account_email" {
  description = "Feed this to `google-github-actions/auth@v2` as `service_account` (a GitHub repo variable)."
  value       = module.gha_oidc.deployer_service_account_email
}

output "monitoring_notification_channel" {
  description = "Resource id of the email notification channel — useful for adding ad-hoc alerts later."
  value       = module.monitoring.notification_channel_id
}
