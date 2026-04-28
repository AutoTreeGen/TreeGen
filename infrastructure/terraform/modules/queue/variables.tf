variable "name" {
  description = "Resource name prefix (e.g. \"staging\")."
  type        = string
}

variable "region" {
  description = "Cloud Tasks location. Pick the same region as Cloud Run to minimize latency."
  type        = string
}
