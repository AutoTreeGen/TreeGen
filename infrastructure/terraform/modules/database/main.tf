// =============================================================================
// database module — AlloyDB Omni on a single Compute Engine VM (staging).
//
// AlloyDB Omni is the downloadable, self-managed AlloyDB engine. For staging
// we run it in Docker on a single e2-standard VM in the private subnet:
//   - cheaper than managed AlloyDB (no min cluster fee, ~$30/mo all-in vs
//     $200+/mo managed) — see ADR-0031 §cost.
//   - same Postgres-superset wire protocol, same SQL surface, same `vector`
//     extension — staging code is unchanged when we cut over to managed
//     AlloyDB in prod.
//   - no automatic failover, no PITR — acceptable for staging where data is
//     synthetic / re-importable from local Ztree.ged.
//
// Bootstrap of the engine itself happens via cloud-init in `metadata.startup-script`.
// The script:
//   1. Installs Docker + alloydb-omni image.
//   2. Initializes data directory on a separate persistent disk.
//   3. Creates the database, application user, and the `vector` extension.
//   4. Stores the application password in Secret Manager for downstream services
//      (the secret resource is created by the secrets module — this module only
//      consumes its name via var.db_password_secret_name).
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

resource "google_compute_address" "alloydb_internal" {
  name         = "${var.name}-alloydb-omni-ip"
  region       = var.region
  subnetwork   = var.private_subnet_id
  address_type = "INTERNAL"
  purpose      = "GCE_ENDPOINT"
}

resource "google_compute_disk" "alloydb_data" {
  name = "${var.name}-alloydb-omni-data"
  type = "pd-ssd"
  zone = var.zone
  size = var.data_disk_gb

  // Data is staging-only and re-importable; no snapshots configured.
  // Production should use the managed AlloyDB resource, not this module.
}

resource "google_service_account" "alloydb_omni" {
  account_id   = "${var.name}-alloydb-omni"
  display_name = "AlloyDB Omni VM SA (${var.name})"
  description  = "Allows the AlloyDB Omni VM to read its bootstrap secret and write logs"
}

resource "google_project_iam_member" "alloydb_omni_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.alloydb_omni.email}"
}

resource "google_project_iam_member" "alloydb_omni_metrics" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.alloydb_omni.email}"
}

// The DB password lives in Secret Manager (created by `secrets` module).
// The VM SA gets read access to that one secret, by name.
resource "google_secret_manager_secret_iam_member" "alloydb_omni_db_password" {
  secret_id = var.db_password_secret_name
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.alloydb_omni.email}"
}

resource "google_compute_instance" "alloydb_omni" {
  name         = "${var.name}-alloydb-omni"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["alloydb-omni"]

  boot_disk {
    initialize_params {
      image = "projects/debian-cloud/global/images/family/debian-12"
      size  = 20
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.alloydb_data.id
    device_name = "alloydb-data"
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = var.private_subnet_id
    network_ip = google_compute_address.alloydb_internal.address
    // No access_config block ⇒ no public IP. Egress goes through Cloud NAT.
  }

  service_account {
    email  = google_service_account.alloydb_omni.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/startup.sh.tftpl", {
    db_name                 = var.db_name
    db_user                 = var.db_user
    db_password_secret_name = var.db_password_secret_name
    project_id              = var.project_id
    alloydb_omni_image      = var.alloydb_omni_image
  })

  // AlloyDB Omni runs in Docker; recreating the VM keeps state on the data
  // disk (separate resource), so a replace is cheap as long as the disk
  // is preserved. lifecycle.create_before_destroy = false because two
  // VMs cannot share the same persistent disk in RW mode.
}
