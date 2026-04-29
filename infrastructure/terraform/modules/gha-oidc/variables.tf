variable "name" {
  description = "Resource name prefix (e.g. \"staging\")."
  type        = string
}

variable "project_id" {
  description = "GCP project ID where the deployer SA lives."
  type        = string
}

variable "github_repository" {
  description = "Repo allowed to use this provider, in `owner/name` form (e.g. \"AutoTreeGen/TreeGen\")."
  type        = string
}

variable "allowed_refs" {
  description = "Refs allowed to impersonate the deployer SA. Empty list = any ref in the repo. Example: [\"refs/heads/main\"]."
  type        = list(string)
  default     = ["refs/heads/main"]
}
