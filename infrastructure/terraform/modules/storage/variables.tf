variable "prefix" {
  description = "Bucket name prefix (e.g. \"autotreegen-staging\"). Bucket names are globally unique — keep specific."
  type        = string
}

variable "location" {
  description = "GCS bucket location (region or multi-region)."
  type        = string
  default     = "EU"
}

variable "force_destroy" {
  description = "If true, terraform destroy deletes non-empty buckets. Use only for ephemeral environments."
  type        = bool
  default     = false
}

variable "kms_key" {
  description = "Optional CMEK key resource name (projects/.../cryptoKeys/...). null = Google-managed encryption."
  type        = string
  default     = null
}
