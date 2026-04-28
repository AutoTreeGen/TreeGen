output "private_ip" {
  description = "Internal IP of the AlloyDB Omni VM (reachable from the private subnet)."
  value       = google_compute_address.alloydb_internal.address
}

output "instance_name" {
  description = "Compute Engine instance name."
  value       = google_compute_instance.alloydb_omni.name
}

output "service_account_email" {
  description = "Service account that the AlloyDB Omni VM runs as."
  value       = google_service_account.alloydb_omni.email
}

output "database_url" {
  description = "Async SQLAlchemy DSN template (password substituted at runtime from Secret Manager)."
  // Password is intentionally referenced as a placeholder; services pull it
  // from Secret Manager at start-up rather than baking it into the URL.
  value = "postgresql+asyncpg://${var.db_user}:__SECRET__@${google_compute_address.alloydb_internal.address}:5432/${var.db_name}"
}

output "host" {
  description = "Hostname/IP for connecting to AlloyDB Omni."
  value       = google_compute_address.alloydb_internal.address
}

output "port" {
  description = "Postgres port (AlloyDB Omni publishes the standard 5432)."
  value       = 5432
}
