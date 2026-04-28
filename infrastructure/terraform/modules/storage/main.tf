// =============================================================================
// storage module — GCS buckets for GED uploads, DNA data, and multimedia.
//
// Buckets:
//   - {prefix}-ged-uploads — raw GEDCOM uploads + parser tmp files.
//                            Lifecycle: delete after 30 d (re-importable).
//   - {prefix}-dna-data    — encrypted DNA payloads. Versioning ON.
//                            Public access prevention enforced.
//   - {prefix}-multimedia  — user photos, scans, audio attached to events.
//                            Lifecycle: nothing automatic (user data).
//
// All buckets are uniform-bucket-level access (UBLA), force_destroy = false
// for prod, true for staging (controlled via var.force_destroy).
//
// Encryption:
//   - DNA bucket uses CMEK if var.kms_key is provided (recommended in prod).
//     Staging defaults to Google-managed keys to avoid the AlloyDB Omni
//     CMEK setup overhead — see ADR-0031.
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

locals {
  ged_uploads = "${var.prefix}-ged-uploads"
  dna_data    = "${var.prefix}-dna-data"
  multimedia  = "${var.prefix}-multimedia"
}

resource "google_storage_bucket" "ged_uploads" {
  name                        = local.ged_uploads
  location                    = var.location
  force_destroy               = var.force_destroy
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = false
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 30
    }
  }
}

resource "google_storage_bucket" "dna_data" {
  name                        = local.dna_data
  location                    = var.location
  force_destroy               = var.force_destroy
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  dynamic "encryption" {
    for_each = var.kms_key == null ? [] : [1]
    content {
      default_kms_key_name = var.kms_key
    }
  }

  // DNA = special-category data (GDPR Art. 9). Hard delete prevention is
  // enforced via versioning + IAM; lifecycle here only manages noncurrent
  // versions to keep storage cost bounded.
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      num_newer_versions = 5
      with_state         = "ARCHIVED"
    }
  }
}

resource "google_storage_bucket" "multimedia" {
  name                        = local.multimedia
  location                    = var.location
  force_destroy               = var.force_destroy
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }
}
