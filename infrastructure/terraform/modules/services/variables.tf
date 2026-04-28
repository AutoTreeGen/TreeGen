variable "name" {
  description = "Resource name prefix (e.g. \"staging\")."
  type        = string
}

variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run services and the VPC connector."
  type        = string
}

variable "vpc_name" {
  description = "Short name of the VPC (from network module)."
  type        = string
}

variable "connector_cidr" {
  description = "/28 CIDR for the Serverless VPC Access connector. Must be unused inside the VPC."
  type        = string
  default     = "10.10.32.0/28"
}

variable "images" {
  description = "Map of service-short-name → container image. Missing entries fall back to var.placeholder_image so the first apply succeeds before CI builds anything."
  type        = map(string)
  default     = {}
}

variable "placeholder_image" {
  description = "Image used by Cloud Run before the first real image is pushed by CI."
  type        = string
  default     = "gcr.io/cloudrun/hello"
}

variable "secret_short_names" {
  description = "Map of logical key → Secret Manager short name (from secrets module)."
  type        = map(string)
}

variable "database_url_template" {
  description = "DSN template for the application DB. Password is injected via DATABASE_PASSWORD secret env var at runtime."
  type        = string
}

variable "cloud_tasks_queue_ids" {
  description = "Map of queue logical name → fully-qualified Cloud Tasks resource id."
  type        = map(string)
}

variable "gcs_bucket_names" {
  description = "GCS bucket names that all service SAs need objectAdmin on."
  type        = list(string)
  default     = []
}

variable "ingress" {
  description = "Cloud Run ingress for non-`web` services. Use INGRESS_TRAFFIC_INTERNAL_ONLY in prod once the LB is in front."
  type        = string
  default     = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
}

variable "allow_public_web" {
  description = "If true, the `web` Cloud Run service is invokable by allUsers (typical for staging where there's no LB)."
  type        = bool
  default     = true
}
