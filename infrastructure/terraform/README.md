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
    └── secrets/                  ← Secret Manager containers (values set out-of-band)
```

The reusable modules are intentionally environment-agnostic. To stand up a
prod environment later, copy `environments/staging/` and adjust:

- `force_destroy = false` for storage,
- swap `database` for the managed AlloyDB resource,
- enable `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` on every Cloud Run service,
- add Cloud Armor and an external HTTPS LB in front of `web`.

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
  cloudresourcemanager.googleapis.com \
  servicenetworking.googleapis.com \
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

## Known follow-ups

- `apps/web/Dockerfile` is structurally complete (multi-stage, standalone
  output, non-root runtime), but the Next.js build currently fails for
  pages that hit the backend at build time (e.g. `/familysearch/connect`).
  Mark those pages with `export const dynamic = "force-dynamic"` (or
  `export const revalidate = 0`) before the staging deploy. Tracking in
  Phase 13.0 follow-up.

## What this scaffolding does *not* do

- No external HTTPS load balancer or Cloud Armor — staging is reached
  directly via Cloud Run's `*.run.app` URL.
- No CMEK on AlloyDB Omni (Google-managed disk encryption only). DNA
  bucket can opt-in via `var.kms_key` once a KMS keyring is provisioned.
- No Identity-Aware Proxy on the admin endpoints — staging assumes the
  owner is the only user.
- No managed AlloyDB. Staging uses AlloyDB Omni on a single VM (cost
  trade-off — see ADR-0031 §AlloyDB).
- No Sentry, no Cloud Audit Log → BigQuery sink. Cloud Logging default
  retention (30 d) is enough for staging.

These belong in the prod environment, which is a separate Terraform root
module to be added in Phase 13.1.
