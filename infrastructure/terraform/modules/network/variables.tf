variable "name" {
  description = "Resource name prefix (e.g. \"staging\", \"prod\")."
  type        = string
}

variable "region" {
  description = "GCP region for the subnets and Cloud NAT."
  type        = string
}

variable "public_cidr" {
  description = "IPv4 CIDR for the public subnet."
  type        = string
  default     = "10.10.0.0/20"
}

variable "private_cidr" {
  description = "IPv4 CIDR for the private subnet (AlloyDB Omni, internal services)."
  type        = string
  default     = "10.10.16.0/20"
}
