// =============================================================================
// staging environment — wires together every module to produce a working
// AutoTreeGen stack on a fresh GCP project.
//
// Apply flow:
//   1. `gcloud services enable …` for required APIs (see README.md §1).
//   2. Bootstrap state bucket (README.md §2).
//   3. terraform init -backend-config=…
//   4. terraform plan -var="project_id=…" -var="bucket_prefix=…"
//   5. terraform apply
//   6. Set initial secret versions out-of-band (README.md §4).
//   7. CI (or manual `gcloud run deploy --image=…`) pushes real images.
//
// Module ordering (Terraform resolves automatically via outputs → inputs):
//
//   network ──┬──► database
//             └──► services ──► (depends on queue, secrets, storage)
//   secrets ──┬──► database (db password binding)
//             └──► services (env-var refs)
//   storage ──► services (GCS IAM)
//   queue   ──► services (queue ids env)
// =============================================================================

module "network" {
  source = "../../modules/network"

  name   = var.name
  region = var.region
}

module "secrets" {
  source = "../../modules/secrets"

  prefix = var.name
  // Accessor SAs are bound via a separate `google_secret_manager_secret_iam_member`
  // block below, after `services` has produced the SA emails. Doing it
  // inside this module would create a cycle (services depends on
  // secret_short_names, secrets would depend on services SAs).
  accessor_service_accounts = []
}

resource "google_secret_manager_secret_iam_member" "service_secret_access" {
  for_each = {
    for pair in setproduct(
      values(module.secrets.secret_short_names),
      values(module.services.service_account_emails),
    ) :
    "${pair[0]}::${pair[1]}" => {
      secret_id = pair[0]
      sa_email  = pair[1]
    }
  }

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.sa_email}"
}

module "storage" {
  source = "../../modules/storage"

  prefix        = "${var.bucket_prefix}-${var.name}"
  location      = var.region
  force_destroy = true // staging is ephemeral
}

module "queue" {
  source = "../../modules/queue"

  name   = var.name
  region = var.region
}

module "database" {
  source = "../../modules/database"

  name                    = var.name
  project_id              = var.project_id
  region                  = var.region
  zone                    = var.zone
  private_subnet_id       = module.network.private_subnet_id
  db_password_secret_name = module.secrets.db_password_name
}

module "services" {
  source = "../../modules/services"

  name               = var.name
  project_id         = var.project_id
  region             = var.region
  vpc_name           = module.network.vpc_name
  images             = var.images
  secret_short_names = module.secrets.secret_short_names

  // DATABASE_URL is rendered with __SECRET__ as a placeholder for the
  // password so the password is injected from Secret Manager at runtime
  // by application code, not stored in Cloud Run env config in plain text.
  // See ADR-0031 §secrets.
  database_url_template = module.database.database_url

  cloud_tasks_queue_ids = module.queue.queue_ids
  gcs_bucket_names      = module.storage.all_bucket_names
}

// Phase 13.1 — GitHub Actions OIDC. CI authenticates via Workload Identity
// Federation, no JSON key checked into the repo.
module "gha_oidc" {
  source = "../../modules/gha-oidc"

  name              = var.name
  project_id        = var.project_id
  github_repository = var.github_repository
  allowed_refs      = var.gha_allowed_refs
}

// Phase 13.1 — log-based metrics + alert policies + email notification channel.
module "monitoring" {
  source = "../../modules/monitoring"

  name               = var.name
  project_id         = var.project_id
  notification_email = var.alert_email

  // Subjects of the alert policies — services whose 5xx / memory we care about.
  cloud_run_service_names = [
    for k, _ in module.services.service_urls : k
  ]
  alloydb_instance_name = module.database.instance_name
}
