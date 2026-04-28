# Terraform — GCP staging deployment

End-to-end Terraform that provisions the AutoTreeGen stack on a fresh GCP
project. See `docs/adr/0031-gcp-deployment-architecture.md` for the
why-behind-the-what.

## Layout

```text
infrastructure/terraform/
├── environments/
│   └── staging/                  ← root module: wires every service together
│       ├── backend.tf            ← GCS remote state (override via -backend-config)
│       ├── main.tf               ← module instantiation + cross-module IAM
│       ├── outputs.tf
│       ├── providers.tf
│       ├── variables.tf
│       └── variables.tfvars.example
└── modules/
    ├── network/                  ← VPC, subnets, Cloud NAT, firewall
    ├── database/                 ← AlloyDB Omni on a single GCE VM
    ├── services/                 ← Cloud Run services + VPC connector + IAM
    ├── queue/                    ← Cloud Tasks queues (imports/hypotheses/notifications)
    ├── storage/                  ← GCS buckets (ged-uploads, dna-data, multimedia)
    ├── secrets/                  ← Secret Manager containers (values set out-of-band)
    ├── gha-oidc/                 ← Workload Identity Federation for GitHub Actions
    └── monitoring/               ← log-based metrics + alert policies + email channel
```

The reusable modules are intentionally environment-agnostic. To stand up a
prod environment later, copy `environments/staging/` and adjust:

- `force_destroy = false` for storage,
- swap `database` for the managed AlloyDB resource,
- enable `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` on every Cloud Run service,
- add Cloud Armor and an external HTTPS LB in front of `web`.

## 0. CI authentication — GitHub Actions OIDC

CI authenticates to GCP via Workload Identity Federation (no JSON keys).
The pool/provider/SA are provisioned by the `gha-oidc` module on first
`terraform apply`. After apply, take two values from `terraform output`
and add them as **GitHub repository variables** (Settings → Secrets and
variables → Variables — *not* Secrets, these are public resource names):

| GitHub variable | Value (from `terraform output`) |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `gha_workload_identity_provider` |
| `GCP_DEPLOY_SA_EMAIL`            | `gha_deployer_service_account_email` |
| `GCP_PROJECT_ID`                 | your project id |
| `GCP_REGION`                     | e.g. `europe-west1` |
| `GCP_ARTIFACT_REGISTRY_REPO`     | e.g. `autotreegen` |

The OIDC trust is restricted to repository `AutoTreeGen/TreeGen` and
ref `refs/heads/main` by default. Adjust via
`var.github_repository` / `var.gha_allowed_refs`.

## 1. One-time GCP project bootstrap

```bash
# Sign in and pick the project.
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_STAGING_PROJECT_ID

# Enable the APIs Terraform needs.
gcloud services enable \
  compute.googleapis.com \
  run.googleapis.com \
  cloudtasks.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  vpcaccess.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  servicenetworking.googleapis.com \
  monitoring.googleapis.com \
  logging.googleapis.com \
  iap.googleapis.com
```

## 2. Bootstrap the Terraform state bucket

State lives in GCS so the local working directory is disposable. Create the
bucket once, with versioning on (so a corrupt apply can be rolled back):

```bash
gcloud storage buckets create gs://YOUR_STAGING_PROJECT_ID-tfstate \
  --location=EU \
  --uniform-bucket-level-access
gcloud storage buckets update gs://YOUR_STAGING_PROJECT_ID-tfstate \
  --versioning
```

Also create the Artifact Registry repository CI will push to:

```bash
gcloud artifacts repositories create autotreegen \
  --repository-format=docker \
  --location=europe-west1 \
  --description="AutoTreeGen container images"
```

## 3. Initialize and apply

```bash
cd infrastructure/terraform/environments/staging
cp variables.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars   # set project_id and bucket_prefix

terraform init \
  -backend-config="bucket=YOUR_STAGING_PROJECT_ID-tfstate" \
  -backend-config="prefix=terraform/state/staging"

terraform plan
terraform apply
```

The first `apply` provisions Cloud Run with a placeholder `gcr.io/cloudrun/hello`
image (because we haven't built any images yet). `apply` will succeed —
services just won't serve real traffic until step 5.

## 4. Set initial secret values

Terraform creates the *containers* in Secret Manager. Values are written
out-of-band so that nothing in `terraform.tfstate` contains plaintext
credentials.

```bash
NAME=staging   # must match var.name

# Pick a long random password for AlloyDB Omni.
DB_PW=$(openssl rand -base64 32)
printf '%s' "$DB_PW" | gcloud secrets versions add "${NAME}-db-password" --data-file=-

# Anthropic Claude API key.
printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add "${NAME}-anthropic-api-key" --data-file=-

# FamilySearch OAuth credentials (developer.familysearch.org).
printf '%s' "$FS_CLIENT_ID"     | gcloud secrets versions add "${NAME}-fs-client-id"     --data-file=-
printf '%s' "$FS_CLIENT_SECRET" | gcloud secrets versions add "${NAME}-fs-client-secret" --data-file=-

# Fernet key for at-rest encryption of FS OAuth tokens (ADR-0027).
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' \
  | gcloud secrets versions add "${NAME}-fs-token-key" --data-file=-

# Envelope-encryption key for DNA segments.
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' \
  | gcloud secrets versions add "${NAME}-encryption-key" --data-file=-
```

Restart the AlloyDB Omni VM once after adding the DB password — the cloud-init
script reads the secret on boot:

```bash
gcloud compute instances reset staging-alloydb-omni --zone=europe-west1-b
```

## 5. Build and push images, then deploy

CI does this automatically on push to `main` (see
`.github/workflows/deploy-staging.yml`). To do it manually from your laptop:

```bash
PROJECT=YOUR_STAGING_PROJECT_ID
REGION=europe-west1
REPO=$REGION-docker.pkg.dev/$PROJECT/autotreegen

gcloud auth configure-docker $REGION-docker.pkg.dev

for SVC in parser-service dna-service notification-service; do
  docker build -f services/$SVC/Dockerfile -t $REPO/$SVC:latest .
  docker push $REPO/$SVC:latest
done
docker build -f apps/web/Dockerfile -t $REPO/web:latest .
docker push $REPO/web:latest

for SVC in parser-service dna-service notification-service web; do
  gcloud run deploy staging-$SVC \
    --region=$REGION \
    --image=$REPO/$SVC:latest
done
```

Cloud Run keeps the `template[0].containers[0].image` change ignored by
Terraform (see `modules/services/main.tf`), so subsequent `terraform apply`
calls will not roll the image back to the placeholder.

## 6. Smoke test

```bash
bash scripts/smoke_test_staging.sh \
  https://staging-web-XXXX.europe-west1.run.app
```

The script `curl`s `/healthz` on each service and walks through a tiny GED
import.

## 7. Rollback

**Application rollback** — pin Cloud Run to the previous image:

```bash
PREV_TAG=$(gcloud run revisions list \
  --service=staging-parser-service \
  --region=europe-west1 \
  --format='value(metadata.name)' \
  --limit=2 | tail -n1)

gcloud run services update-traffic staging-parser-service \
  --region=europe-west1 \
  --to-revisions=$PREV_TAG=100
```

**Infrastructure rollback** — Terraform state has versioning; restore:

```bash
gcloud storage objects list \
  "gs://YOUR_STAGING_PROJECT_ID-tfstate/terraform/state/staging/default.tfstate" \
  --include-noncurrent

# Pick a generation, then:
gcloud storage cp \
  "gs://...tfstate/terraform/state/staging/default.tfstate#GENERATION" \
  "gs://...tfstate/terraform/state/staging/default.tfstate"
```

**Full teardown (staging only)** — `force_destroy = true` is set on every
bucket in staging, so this removes everything including data:

```bash
terraform destroy
```

> Never run `terraform destroy` against a prod environment — bucket
> `force_destroy` should be flipped to `false` in the prod copy of this
> module to make accidental deletion impossible.

## 8. Monitoring & alerts (Phase 13.1)

The `monitoring` module ships three alert policies bound to one email
channel (`var.alert_email`):

- Cloud Run 5xx > 5% over 5 min.
- AlloyDB Omni VM CPU > 80% for 10 min.
- Cloud Run container memory > 90% for 5 min.

After first apply, **confirm the email** — Google sends a verification link
to `var.alert_email`. Until you click it, alerts are silently dropped.

To wire Sentry, add the DSN as a Cloud Run env var (the apps init Sentry
only when `SENTRY_DSN` is set):

```bash
for SVC in parser-service dna-service notification-service; do
  gcloud run services update staging-$SVC \
    --region=europe-west1 \
    --update-env-vars=SENTRY_DSN=https://YOUR_DSN@sentry.io/PROJECT
done
```

Removing the env var brings the services back to no-Sentry mode (no redeploy).

## What this scaffolding does *not* do

- No external HTTPS load balancer or Cloud Armor — staging is reached
  directly via Cloud Run's `*.run.app` URL.
- No CMEK on AlloyDB Omni (Google-managed disk encryption only). DNA
  bucket can opt-in via `var.kms_key` once a KMS keyring is provisioned.
- No Identity-Aware Proxy on the admin endpoints — staging assumes the
  owner is the only user.
- No managed AlloyDB. Staging uses AlloyDB Omni on a single VM (cost
  trade-off — see ADR-0031 §AlloyDB).
- No Cloud Audit Log → BigQuery sink. Cloud Logging default
  retention (30 d) is enough for staging.

These belong in the prod environment, which is a separate Terraform root
module deferred to Phase 13.2.
