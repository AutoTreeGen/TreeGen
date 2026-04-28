// =============================================================================
// services module — Cloud Run v2 services + Serverless VPC Access connector.
//
// Provisions four Cloud Run services that mirror the local stack:
//   - parser-service        (FastAPI + arq HTTP shim, GED imports)
//   - dna-service           (FastAPI, encrypted DNA storage)
//   - notification-service  (FastAPI, fan-out to email/webhook)
//   - web                   (Next.js standalone, public-facing)
//
// Image names are parameterized so CI (deploy-staging.yml) can push new
// digests and re-apply, or so the operator can `gcloud run deploy --image=...`
// directly. Initial apply uses the placeholder image from var.placeholder_image
// (gcr.io/cloudrun/hello) so terraform apply succeeds before the first build.
//
// Each service runs as its own SA. Cloud Run egresses through the VPC
// connector to reach AlloyDB Omni in the private subnet.
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

resource "google_vpc_access_connector" "connector" {
  name          = "${var.name}-vpc-conn"
  region        = var.region
  network       = var.vpc_name
  ip_cidr_range = var.connector_cidr
  min_instances = 2
  max_instances = 3
  machine_type  = "e2-micro"
}

locals {
  // Phase 13.1 — common observability env vars for every Cloud Run service.
  // Both are read by `shared_models.observability` at startup; if
  // SENTRY_DSN is empty, init_sentry is a no-op.
  observability_env = {
    LOG_FORMAT_JSON = "true"
    ENVIRONMENT     = var.name
  }

  // Service definitions — keep the secret list per-service narrow
  // (least privilege). `web` doesn't need the Anthropic key; only
  // parser-service touches FamilySearch.
  services = {
    "parser-service" = {
      image         = lookup(var.images, "parser-service", var.placeholder_image)
      port          = 8000
      cpu           = "1"
      memory        = "1Gi"
      max_instances = 5
      env_extra     = local.observability_env
      secrets = {
        DATABASE_PASSWORD               = var.secret_short_names["db-password"]
        ANTHROPIC_API_KEY               = var.secret_short_names["anthropic-api-key"]
        PARSER_SERVICE_FS_CLIENT_ID     = var.secret_short_names["fs-client-id"]
        PARSER_SERVICE_FS_CLIENT_SECRET = var.secret_short_names["fs-client-secret"]
        PARSER_SERVICE_FS_TOKEN_KEY     = var.secret_short_names["fs-token-key"]
      }
    }
    "dna-service" = {
      image         = lookup(var.images, "dna-service", var.placeholder_image)
      port          = 8001
      cpu           = "1"
      memory        = "1Gi"
      max_instances = 3
      env_extra     = local.observability_env
      secrets = {
        DATABASE_PASSWORD = var.secret_short_names["db-password"]
        ENCRYPTION_KEY    = var.secret_short_names["encryption-key"]
      }
    }
    "notification-service" = {
      image         = lookup(var.images, "notification-service", var.placeholder_image)
      port          = 8002
      cpu           = "1"
      memory        = "512Mi"
      max_instances = 3
      env_extra     = local.observability_env
      secrets = {
        DATABASE_PASSWORD = var.secret_short_names["db-password"]
      }
    }
    "web" = {
      image         = lookup(var.images, "web", var.placeholder_image)
      port          = 3000
      cpu           = "1"
      memory        = "512Mi"
      max_instances = 5
      env_extra     = local.observability_env
      secrets       = {}
    }
  }
}

resource "google_service_account" "service" {
  for_each = local.services

  account_id   = "${var.name}-${each.key}"
  display_name = "Cloud Run runtime SA for ${each.key} (${var.name})"
}

// Each Cloud Run SA needs Cloud Tasks enqueuer to push jobs.
resource "google_project_iam_member" "tasks_enqueuer" {
  for_each = local.services

  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"
  member  = "serviceAccount:${google_service_account.service[each.key].email}"
}

// Cloud Tasks needs to invoke Cloud Run services as the worker (pull
// queue consumer pattern is not used; we use HTTP push). Each service
// SA can act as a token-minting principal for its own HTTP target.
resource "google_project_iam_member" "service_account_user" {
  for_each = local.services

  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.service[each.key].email}"
}

// Logs and metrics writers (so JSON logs land in Cloud Logging).
resource "google_project_iam_member" "logs_writer" {
  for_each = local.services

  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.service[each.key].email}"
}

resource "google_project_iam_member" "metrics_writer" {
  for_each = local.services

  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.service[each.key].email}"
}

// Cloud Storage object access — scoped to the GCS bucket names from the
// storage module. Granted at bucket level (not project) for least privilege.
resource "google_storage_bucket_iam_member" "service_bucket_access" {
  for_each = {
    for pair in setproduct(keys(local.services), var.gcs_bucket_names) :
    "${pair[0]}::${pair[1]}" => {
      service = pair[0]
      bucket  = pair[1]
    }
  }

  bucket = each.value.bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.service[each.value.service].email}"
}

resource "google_cloud_run_v2_service" "service" {
  for_each = local.services

  name     = "${var.name}-${each.key}"
  location = var.region
  // staging is internal-only by default; flip via var.ingress for public web.
  ingress = each.key == "web" ? "INGRESS_TRAFFIC_ALL" : var.ingress

  template {
    service_account = google_service_account.service[each.key].email

    scaling {
      min_instance_count = 0
      max_instance_count = each.value.max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = each.value.image

      ports {
        container_port = each.value.port
      }

      resources {
        limits = {
          cpu    = each.value.cpu
          memory = each.value.memory
        }
        // CPU only allocated during request — staging cost optimization.
        cpu_idle          = true
        startup_cpu_boost = true
      }

      // Common env: DB host/name, queue backend, etc.
      env {
        name  = "DATABASE_URL"
        value = var.database_url_template
      }
      env {
        name  = "PARSER_SERVICE_QUEUE_BACKEND"
        value = "cloud_tasks"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_LOCATION"
        value = var.region
      }
      env {
        name  = "CLOUD_TASKS_QUEUE_IMPORTS"
        value = var.cloud_tasks_queue_ids["imports"]
      }
      env {
        name  = "CLOUD_TASKS_QUEUE_HYPOTHESES"
        value = var.cloud_tasks_queue_ids["hypotheses"]
      }
      env {
        name  = "CLOUD_TASKS_QUEUE_NOTIFICATIONS"
        value = var.cloud_tasks_queue_ids["notifications"]
      }

      dynamic "env" {
        for_each = each.value.env_extra
        content {
          name  = env.key
          value = env.value
        }
      }

      // Secret refs — exposed as env vars at runtime, latest version pinned
      // by Cloud Run. Rotation = new secret version; no redeploy needed.
      dynamic "env" {
        for_each = each.value.secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = each.value.port
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }
    }
  }

  // Lifecycle: ignore image changes after creation. CI will deploy new images
  // with `gcloud run deploy --image=...`; terraform should not roll them back
  // on subsequent applies.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

// Public ingress for `web` only (others are reached via Cloud Tasks or the
// internal load balancer in prod).
resource "google_cloud_run_v2_service_iam_member" "web_public" {
  count = var.allow_public_web ? 1 : 0

  project  = google_cloud_run_v2_service.service["web"].project
  location = google_cloud_run_v2_service.service["web"].location
  name     = google_cloud_run_v2_service.service["web"].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
