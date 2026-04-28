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
    ged_uploads = module.storage.ged_uploads_bucket
    dna_data    = module.storage.dna_data_bucket
    multimedia  = module.storage.multimedia_bucket
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
