variable "project_id" {
  description = "GCP project ID hosting the staging environment."
  type        = string
}

variable "region" {
  description = "Primary region for Cloud Run, Cloud Tasks, AlloyDB Omni VM, GCS."
  type        = string
  default     = "europe-west1"
}

variable "zone" {
  description = "Zone for the AlloyDB Omni VM and its persistent disk."
  type        = string
  default     = "europe-west1-b"
}

variable "name" {
  description = "Environment name used as resource prefix."
  type        = string
  default     = "staging"
}

variable "bucket_prefix" {
  description = "GCS bucket name prefix. Bucket names are globally unique — use the project id."
  type        = string
}

variable "images" {
  description = "Optional map of service short name → image. Empty map ⇒ all services start on the placeholder hello image; CI then deploys real digests post-apply."
  type        = map(string)
  default     = {}
}
