// =============================================================================
// network module — VPC, subnets, firewall, Cloud NAT.
//
// Layout:
//   - One VPC per environment (custom subnet mode, no auto-create).
//   - Two subnets:
//       * `${name}-public`  — Cloud Run egress, load balancer NEG.
//       * `${name}-private` — AlloyDB Omni VM, private services.
//   - Cloud Router + Cloud NAT for egress from private subnet
//     (so the AlloyDB VM can reach Artifact Registry / Secret Manager
//     without a public IP).
//   - Firewall rules:
//       * allow-internal (within VPC, all protocols).
//       * allow-iap-ssh  (35.235.240.0/20 → port 22 over IAP).
//
// Cloud Run uses serverless VPC connector (not provisioned here — done
// inside `services` module to keep responsibility narrow). Connector
// targets the `${name}-private` subnet.
// =============================================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.40, < 7.0"
    }
  }
}

resource "google_compute_network" "vpc" {
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  description             = "AutoTreeGen ${var.name} VPC"
}

resource "google_compute_subnetwork" "public" {
  name          = "${var.name}-public"
  network       = google_compute_network.vpc.id
  region        = var.region
  ip_cidr_range = var.public_cidr
  // Enables flow logs for SOC visibility (cheap on staging traffic levels).
  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_subnetwork" "private" {
  name                     = "${var.name}-private"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  ip_cidr_range            = var.private_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_router" "router" {
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.private.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

resource "google_compute_firewall" "allow_internal" {
  name    = "${var.name}-fw-allow-internal"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }

  source_ranges = [var.public_cidr, var.private_cidr]
  description   = "Allow all intra-VPC traffic between subnets"
}

resource "google_compute_firewall" "allow_iap_ssh" {
  name    = "${var.name}-fw-allow-iap-ssh"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  // IAP TCP forwarder source range — only Google's IAP service can SSH in.
  source_ranges = ["35.235.240.0/20"]
  description   = "Allow SSH from IAP for AlloyDB Omni VM administration"
  target_tags   = ["alloydb-omni"]
}
