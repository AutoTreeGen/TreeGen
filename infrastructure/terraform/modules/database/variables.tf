variable "name" {
  description = "Resource name prefix (e.g. \"staging\")."
  type        = string
}

variable "project_id" {
  description = "GCP project ID hosting the VM."
  type        = string
}

variable "region" {
  description = "GCP region for the static internal IP."
  type        = string
}

variable "zone" {
  description = "GCP zone for the AlloyDB Omni VM and its data disk."
  type        = string
}

variable "private_subnet_id" {
  description = "Self-link of the private subnet (from network module)."
  type        = string
}

variable "machine_type" {
  description = "Compute Engine machine type for the AlloyDB Omni VM."
  type        = string
  default     = "e2-standard-2"
}

variable "data_disk_gb" {
  description = "Size of the persistent SSD data disk in GiB."
  type        = number
  default     = 50
}

variable "db_name" {
  description = "Application database name to create on first boot."
  type        = string
  default     = "autotreegen"
}

variable "db_user" {
  description = "Application database user to create on first boot."
  type        = string
  default     = "autotreegen"
}

variable "db_password_secret_name" {
  description = "Secret Manager secret short name (not full resource path) holding the DB password."
  type        = string
}

variable "alloydb_omni_image" {
  description = "Docker image for AlloyDB Omni. Pin to a specific version in tfvars."
  type        = string
  default     = "gcr.io/alloydb-omni/alloydbomni:15.7.0"
}
