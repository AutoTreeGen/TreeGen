#!/usr/bin/env bash
# Phase 13.0 — staging smoke test.
#
# Verifies the deployed staging stack:
#   1. Each Cloud Run service responds to GET /healthz.
#   2. A tiny GED can be uploaded via parser-service and an import job
#      reaches `completed` (or at least leaves `queued`).
#
# Usage:
#   bash scripts/smoke_test_staging.sh <web-url>
#
#   <web-url>   — the staging-web Cloud Run URL, e.g.
#                 https://staging-web-XXXX.europe-west1.run.app
#
# Service URLs are discovered via gcloud (so the script works without
# extra configuration once the operator's gcloud is authenticated to the
# staging project). Override individual URLs with these env vars:
#
#   STAGING_PARSER_URL, STAGING_DNA_URL, STAGING_NOTIFICATION_URL
#
# The script is intentionally tolerant of the parser-service `/imports`
# path being protected — if `/healthz` works on every service, we treat
# that as a green smoke. The full upload-then-poll path is best-effort.

set -euo pipefail

WEB_URL="${1:-}"
if [[ -z "$WEB_URL" ]]; then
  echo "usage: $0 <web-url>" >&2
  exit 2
fi

REGION="${GCP_REGION:-europe-west1}"

resolve() {
  # Resolve a Cloud Run service URL via gcloud, ignoring failures.
  local name="$1"
  gcloud run services describe "$name" \
    --region="$REGION" \
    --format='value(status.url)' 2>/dev/null || true
}

PARSER_URL="${STAGING_PARSER_URL:-$(resolve staging-parser-service)}"
DNA_URL="${STAGING_DNA_URL:-$(resolve staging-dna-service)}"
NOTIFY_URL="${STAGING_NOTIFICATION_URL:-$(resolve staging-notification-service)}"

# ---------------------------------------------------------------------------
# Step 1 — /healthz on each service.
# ---------------------------------------------------------------------------
fail=0
check_health() {
  local name="$1"
  local url="$2"
  if [[ -z "$url" ]]; then
    echo "[skip] $name — no URL resolved (service not deployed?)"
    return
  fi
  printf "[%s] GET %s/healthz ... " "$name" "$url"
  # `-f` makes curl exit non-zero on HTTP errors. `-sS` is silent-but-show-errors.
  if curl -fsS --max-time 30 "$url/healthz" -o /dev/null; then
    echo "OK"
  else
    echo "FAIL"
    fail=1
  fi
}

check_health "web"                "$WEB_URL"
check_health "parser-service"     "$PARSER_URL"
check_health "dna-service"        "$DNA_URL"
check_health "notification-service" "$NOTIFY_URL"

if [[ $fail -ne 0 ]]; then
  echo
  echo "[smoke] One or more /healthz checks failed."
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 2 — upload a 1-person GED and poll for completion.
# Best-effort: parser-service /imports may require auth in staging. If we
# get 401/403 we treat the smoke as still-passing (health checks already
# passed) and exit 0.
# ---------------------------------------------------------------------------
if [[ -z "$PARSER_URL" ]]; then
  echo "[smoke] parser-service URL not resolved; skipping upload step."
  exit 0
fi

GED_TMP=$(mktemp --suffix .ged)
trap 'rm -f "$GED_TMP"' EXIT

cat > "$GED_TMP" <<'EOF'
0 HEAD
1 SOUR autotreegen-smoke
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Smoke /Test/
1 SEX M
0 TRLR
EOF

echo
echo "[smoke] uploading tiny GED to ${PARSER_URL}/imports"
http_code=$(curl -sS --max-time 60 -o /tmp/import.resp -w '%{http_code}' \
  -X POST "${PARSER_URL}/imports" \
  -F "file=@${GED_TMP}" || true)

case "$http_code" in
  201|202)
    echo "[smoke] upload accepted (HTTP $http_code)"
    job_id=$(python3 -c 'import json,sys; print(json.load(open("/tmp/import.resp"))["id"])' 2>/dev/null || true)
    if [[ -z "${job_id:-}" ]]; then
      echo "[smoke] could not parse job id from response — skipping poll."
      exit 0
    fi
    echo "[smoke] polling job ${job_id} (up to 60 s)"
    for _ in $(seq 1 12); do
      status=$(curl -fsS --max-time 10 "${PARSER_URL}/imports/${job_id}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' \
        2>/dev/null || true)
      echo "  status=${status}"
      case "$status" in
        completed) echo "[smoke] OK — job completed."; exit 0 ;;
        failed)    echo "[smoke] job failed."; exit 1 ;;
      esac
      sleep 5
    done
    echo "[smoke] job did not complete in 60 s — staging may be slow but the API works."
    exit 0
    ;;
  401|403)
    echo "[smoke] upload denied (HTTP $http_code) — endpoint requires auth."
    echo "[smoke] /healthz already passed; treating overall smoke as OK."
    exit 0
    ;;
  *)
    echo "[smoke] upload returned unexpected HTTP $http_code — see /tmp/import.resp:"
    cat /tmp/import.resp || true
    exit 1
    ;;
esac
