output "vpc_id" {
  description = "Self-link of the created VPC."
  value       = google_compute_network.vpc.id
}

output "vpc_name" {
  description = "Short name of the VPC."
  value       = google_compute_network.vpc.name
}

output "public_subnet_id" {
  description = "Self-link of the public subnet."
  value       = google_compute_subnetwork.public.id
}

output "public_subnet_name" {
  description = "Short name of the public subnet."
  value       = google_compute_subnetwork.public.name
}

output "private_subnet_id" {
  description = "Self-link of the private subnet."
  value       = google_compute_subnetwork.private.id
}

output "private_subnet_name" {
  description = "Short name of the private subnet."
  value       = google_compute_subnetwork.private.name
}

output "private_subnet_cidr" {
  description = "CIDR range of the private subnet."
  value       = google_compute_subnetwork.private.ip_cidr_range
}
