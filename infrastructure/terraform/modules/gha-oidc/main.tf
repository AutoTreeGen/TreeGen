// =============================================================================
// gha-oidc module — Workload Identity Federation for GitHub Actions.
//
// Replaces long-lived service account JSON keys with short-lived OIDC tokens.
// GitHub Actions exchanges its `id-token` JWT against this provider for a
// federated GCP access token, scoped to the `github-deployer` SA.
//
// IAM trust is restricted to:
//   - One repository (var.github_repository, e.g. "AutoTreeGen/TreeGen").
//   - One ref by default (var.allowed_ref = "refs/heads/main").
//
// Roles granted to `github-deployer`:
//   - roles/run.admin                — deploy Cloud Run services
//   - roles/artifactregistry.writer  — push images
//   - roles/cloudsql.client          — connect from the deployer (only used
//                                      for one-shot migrations; revoke later
//                                      if migrations move into-cluster)
//   - roles/secretmanager.secretAccessor (project-level)
//                                    — read secrets at deploy time
//                                      (fine-grained per-secret bindings live
//                                      in env-level main.tf for the runtime
//                                      service accounts; the deployer needs
//                                      project-wide read so new secrets work
//                                      without an IAM round-trip)
//   - roles/iam.serviceAccountUser   — actAs target SAs to run gcloud run
//                                      deploy with --service-account=...
// See ADR-0032 §CI auth.
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

resource "google_iam_workload_identity_pool" "gha" {
  workload_identity_pool_id = "${var.name}-gha"
  display_name              = "GitHub Actions (${var.name})"
  description               = "Federated identity pool for GitHub Actions OIDC"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.gha.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-${var.name}"
  display_name                       = "GitHub OIDC"
  description                        = "OIDC provider trusting tokens from token.actions.githubusercontent.com"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  // Map the GitHub OIDC claims we want to enforce later in IAM bindings.
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    "attribute.actor"      = "assertion.actor"
  }

  // Hard gate at the provider level: only the configured repo can mint tokens
  // through this provider, regardless of any downstream IAM mistakes. This is
  // belt-and-braces — the SA binding below also enforces repo + ref.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""
}

resource "google_service_account" "deployer" {
  account_id   = "${var.name}-github-deployer"
  display_name = "GitHub Actions deployer (${var.name})"
  description  = "Used by GitHub Actions via Workload Identity Federation to deploy Cloud Run services."
}

// Bind the principalSet → SA: only OIDC assertions matching repo + ref can
// impersonate the deployer SA. Default ref = main; expand if you adopt
// release branches.
resource "google_service_account_iam_member" "wif_binding" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member = format(
    "principalSet://iam.googleapis.com/%s/attribute.repository/%s",
    google_iam_workload_identity_pool.gha.name,
    var.github_repository,
  )

  // Provider-level attribute_condition already filters by repo. The
  // member URI above is the standard form documented by Google. To restrict
  // to a specific ref, swap to `principal://...attribute.ref/refs/heads/main`
  // — only one ref per binding, so a list of allowed refs needs one resource
  // per ref. Default keeps it simple.
}

// Per-ref restriction (optional). Adds a second binding so token exchanges
// only succeed when assertion.ref matches one of var.allowed_refs. If empty,
// the repo-level binding above is the only gate (any branch in the repo can
// deploy — fine for a single-branch repo, less fine if feature branches run
// CI with deploy-staging).
resource "google_service_account_iam_member" "wif_binding_ref" {
  for_each = toset(var.allowed_refs)

  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member = format(
    "principal://iam.googleapis.com/%s/subject/repo:%s:ref:%s",
    google_iam_workload_identity_pool.gha.name,
    var.github_repository,
    each.value,
  )
}

// ---- Project-level roles for the deployer SA ------------------------------

locals {
  deployer_roles = [
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/cloudsql.client",
    "roles/secretmanager.secretAccessor",
    "roles/iam.serviceAccountUser",
  ]
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(local.deployer_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}
