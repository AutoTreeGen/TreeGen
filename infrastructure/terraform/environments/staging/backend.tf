// Remote state — GCS bucket. Bucket and prefix must already exist
// (bootstrap step in README.md §2). Versioning ON on the bucket gives
// us state-file rollback if a bad apply corrupts state.
//
// `bucket` and `prefix` cannot reference variables — they must be passed
// via `terraform init -backend-config=…` or hardcoded. See README.md.

terraform {
  backend "gcs" {
    // bucket = "autotreegen-staging-tfstate"   ← override via -backend-config
    // prefix = "terraform/state/staging"       ← override via -backend-config
  }
}
