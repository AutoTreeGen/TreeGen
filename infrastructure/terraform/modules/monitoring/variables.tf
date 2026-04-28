variable "name" {
  description = "Resource name prefix (e.g. \"staging\")."
  type        = string
}

variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "notification_email" {
  description = "Email address that receives alert notifications. Must be confirmed in Cloud Monitoring after first apply."
  type        = string
}

variable "cloud_run_service_names" {
  description = "Cloud Run service short names this module monitors. Currently informational — alerts are at project scope, not per-service. Keep for future per-service policies."
  type        = list(string)
  default     = []
}

variable "alloydb_instance_name" {
  description = "Compute Engine instance name of the AlloyDB Omni VM (from the database module)."
  type        = string
}
