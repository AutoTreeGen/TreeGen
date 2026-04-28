output "service_urls" {
  description = "Map of service short name → Cloud Run URL."
  value = {
    for k, s in google_cloud_run_v2_service.service : k => s.uri
  }
}

output "service_account_emails" {
  description = "Map of service short name → runtime service account email. Wire these into secrets module's accessor_service_accounts."
  value = {
    for k, sa in google_service_account.service : k => sa.email
  }
}

output "vpc_connector_id" {
  description = "Self-link of the Serverless VPC Access connector."
  value       = google_vpc_access_connector.connector.id
}
