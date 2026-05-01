#!/usr/bin/env bash
# Phase 10.9a — voice-to-tree staging deploy helper.
#
# Что делает (паритет с staging-deploy-10-9a.ps1):
#   1. Проверяет prereq'ы: gcloud auth, terraform CLI, OPENAI_API_KEY env var.
#   2. Создаёт / обновляет секрет OPENAI_API_KEY в Secret Manager.
#   3. terraform apply в environments/staging — создаёт audio_sessions bucket.
#   4. Привязывает секрет к Cloud Run revision parser-service (--update-secrets).
#   5. Подсказывает команду alembic upgrade head (DB-доступ owner'а вне script'а).
#   6. Триггерит новую revision parser-service.
#   7. Smoke: GET /healthz parser-service.
#
# Usage:
#   OPENAI_API_KEY="sk-..." \
#   bash scripts/staging-deploy-10-9a.sh \
#     --project-id <gcp-project> \
#     [--region europe-west1] \
#     [--service-name staging-parser-service] \
#     [--dry-run | --confirm]
#
# `--dry-run` печатает команды без выполнения. `--confirm` обязателен для
# реального применения; без него скрипт остановится после prereq-проверок.
#
# OPENAI_API_KEY читается ТОЛЬКО из env-переменной (никогда из CLI args /
# файлов / heredoc'ов в скрипте — иначе попадёт в shell history).

set -euo pipefail

# ---- defaults --------------------------------------------------------------
REGION="europe-west1"
SERVICE_NAME="staging-parser-service"
PROJECT_ID=""
DRY_RUN=0
CONFIRM=0

# ---- arg parsing -----------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)   PROJECT_ID="$2"; shift 2 ;;
    --region)       REGION="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --dry-run)      DRY_RUN=1; shift ;;
    --confirm)      CONFIRM=1; shift ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "[error] unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ---- step helpers ----------------------------------------------------------
say()  { printf '[deploy] %s\n' "$*"; }
warn() { printf '[deploy:WARN] %s\n' "$*" >&2; }
fail() { printf '[deploy:ERROR] %s\n' "$*" >&2; exit 1; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    eval "$@"
  fi
}

# ---- step 1 — prereq checks -----------------------------------------------
say "step 1/7 — prereq checks"

[[ -n "$PROJECT_ID" ]] || fail "--project-id required (e.g. autotreegen-staging-1)"
[[ -n "${OPENAI_API_KEY:-}" ]] || fail "OPENAI_API_KEY env var must be set (do NOT pass via CLI)"

command -v gcloud >/dev/null 2>&1 || fail "gcloud CLI not found in PATH"
command -v terraform >/dev/null 2>&1 || fail "terraform CLI not found in PATH"
command -v curl >/dev/null 2>&1 || fail "curl not found in PATH"

ACTIVE_ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
[[ -n "$ACTIVE_ACCOUNT" ]] || fail "gcloud not authenticated; run 'gcloud auth login'"
ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ "$ACTIVE_PROJECT" != "$PROJECT_ID" ]]; then
  warn "active gcloud project '$ACTIVE_PROJECT' != --project-id '$PROJECT_ID'"
  warn "set with: gcloud config set project $PROJECT_ID"
fi

say "  account=$ACTIVE_ACCOUNT  project=$PROJECT_ID  region=$REGION"

if [[ $CONFIRM -ne 1 && $DRY_RUN -ne 1 ]]; then
  warn "no --confirm or --dry-run flag — stopping after prereq check"
  warn "re-run with --dry-run to preview, or --confirm to apply"
  exit 0
fi

# ---- step 2 — OPENAI_API_KEY secret ---------------------------------------
say "step 2/7 — OPENAI_API_KEY in Secret Manager"

if gcloud secrets describe OPENAI_API_KEY --project="$PROJECT_ID" >/dev/null 2>&1; then
  say "  secret exists — adding new version"
  run "printf '%s' \"\$OPENAI_API_KEY\" | gcloud secrets versions add OPENAI_API_KEY --project='$PROJECT_ID' --data-file=-"
else
  say "  secret missing — creating with replication=automatic"
  run "gcloud secrets create OPENAI_API_KEY --project='$PROJECT_ID' --replication-policy=automatic"
  run "printf '%s' \"\$OPENAI_API_KEY\" | gcloud secrets versions add OPENAI_API_KEY --project='$PROJECT_ID' --data-file=-"
fi

# ---- step 3 — terraform apply (audio bucket) ------------------------------
say "step 3/7 — terraform apply environments/staging"

TF_DIR="infrastructure/terraform/environments/staging"
[[ -d "$TF_DIR" ]] || fail "terraform dir not found: $TF_DIR (run from repo root)"

run "terraform -chdir='$TF_DIR' init -input=false"
if [[ $DRY_RUN -eq 1 ]]; then
  run "terraform -chdir='$TF_DIR' plan -input=false -target=google_storage_bucket.audio_sessions"
else
  run "terraform -chdir='$TF_DIR' apply -input=false -auto-approve -target=google_storage_bucket.audio_sessions"
fi

# ---- step 4 — bind secret to Cloud Run -----------------------------------
say "step 4/7 — wire OPENAI_API_KEY into Cloud Run revision"

run "gcloud run services update '$SERVICE_NAME' --region='$REGION' --project='$PROJECT_ID' --update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest"

# ---- step 5 — alembic upgrade reminder -----------------------------------
say "step 5/7 — DB migration reminder"

cat <<EOF
  [ACTION REQUIRED] alembic migration runs against staging DB outside this script.
  Connect via the AlloyDB Omni VM (private IP) using IAM auth, then:
    DATABASE_URL='<staging-dsn>' uv run alembic upgrade head
  Confirm migration 0030 applied:
    DATABASE_URL='<staging-dsn>' uv run alembic current
EOF

# ---- step 6 — trigger new revision ---------------------------------------
say "step 6/7 — trigger new parser-service revision"

run "gcloud run services update '$SERVICE_NAME' --region='$REGION' --project='$PROJECT_ID' --update-labels=phase=10-9a"

# ---- step 7 — smoke test --------------------------------------------------
say "step 7/7 — smoke /healthz on $SERVICE_NAME"

if [[ $DRY_RUN -eq 1 ]]; then
  run "gcloud run services describe '$SERVICE_NAME' --region='$REGION' --project='$PROJECT_ID' --format='value(status.url)'"
else
  PARSER_URL="$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')"
  [[ -n "$PARSER_URL" ]] || fail "could not resolve $SERVICE_NAME URL"
  say "  GET $PARSER_URL/healthz"
  if curl -fsS --max-time 30 "$PARSER_URL/healthz" -o /dev/null; then
    say "  /healthz OK"
  else
    fail "  /healthz failed — check 'gcloud run services logs read $SERVICE_NAME --region=$REGION'"
  fi
fi

say "done. Next: run e2e demo rehearsal — see docs/runbooks/demo-rehearsal-2026-05-06.md"
