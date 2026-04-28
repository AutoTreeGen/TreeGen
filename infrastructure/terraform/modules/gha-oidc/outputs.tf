output "workload_identity_pool_id" {
  description = "Full resource name of the WIF pool, e.g. projects/.../locations/global/workloadIdentityPools/staging-gha."
  value       = google_iam_workload_identity_pool.gha.name
}

output "workload_identity_provider" {
  description = "Full resource name of the OIDC provider — feed this directly to `google-github-actions/auth@v2` as `workload_identity_provider`."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account_email" {
  description = "Email of the deployer SA — feed to `google-github-actions/auth@v2` as `service_account`."
  value       = google_service_account.deployer.email
}
