output "ged_uploads_bucket" {
  description = "Name of the GED uploads bucket."
  value       = google_storage_bucket.ged_uploads.name
}

output "dna_data_bucket" {
  description = "Name of the DNA data bucket (encrypted, versioned)."
  value       = google_storage_bucket.dna_data.name
}

output "multimedia_bucket" {
  description = "Name of the multimedia bucket."
  value       = google_storage_bucket.multimedia.name
}

output "all_bucket_names" {
  description = "All bucket names — convenient for IAM bindings in services module."
  value = [
    google_storage_bucket.ged_uploads.name,
    google_storage_bucket.dna_data.name,
    google_storage_bucket.multimedia.name,
  ]
}
