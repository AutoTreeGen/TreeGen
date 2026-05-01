<#
.SYNOPSIS
Развернуть Phase 10.9a (voice-to-tree) на staging-кластере.

.DESCRIPTION
Паритет с staging-deploy-10-9a.sh. Шаги:
  1. Проверка prereq'ов (gcloud auth, terraform, OPENAI_API_KEY env).
  2. Создание / обновление секрета OPENAI_API_KEY в Secret Manager.
  3. terraform apply environments/staging (audio_sessions bucket).
  4. Привязка секрета к Cloud Run revision parser-service.
  5. Подсказка по alembic upgrade head на staging-БД.
  6. Trigger новой revision parser-service.
  7. Smoke /healthz.

.PREREQUISITES
- gcloud authenticated на staging GCP project
- terraform CLI в PATH
- $env:OPENAI_API_KEY установлен (никогда не передавать через args / файлы)

.PARAMETER ProjectId
GCP project ID (обязательный).

.PARAMETER Region
GCP region. Default: europe-west1.

.PARAMETER ServiceName
Cloud Run service name. Default: staging-parser-service.

.PARAMETER DryRun
Печатает команды без выполнения.

.PARAMETER Confirm
Обязательный флаг для реального применения. Без -DryRun или -Confirm
скрипт остановится после prereq-проверок.

.EXAMPLE
$env:OPENAI_API_KEY = "sk-..."
./scripts/staging-deploy-10-9a.ps1 -ProjectId autotreegen-staging-1 -DryRun

.EXAMPLE
$env:OPENAI_API_KEY = "sk-..."
./scripts/staging-deploy-10-9a.ps1 -ProjectId autotreegen-staging-1 -Confirm
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "europe-west1",
    [string]$ServiceName = "staging-parser-service",
    [switch]$DryRun,
    [switch]$Confirm
)

$ErrorActionPreference = "Stop"

function Write-Step { Write-Host "[deploy] $($args -join ' ')" }
function Write-Warn { Write-Host "[deploy:WARN] $($args -join ' ')" -ForegroundColor Yellow }
function Write-Fail { Write-Host "[deploy:ERROR] $($args -join ' ')" -ForegroundColor Red; exit 1 }

function Invoke-Step {
    param([string]$Command)
    if ($DryRun) {
        Write-Host "[dry-run] $Command"
    } else {
        Invoke-Expression $Command
        if ($LASTEXITCODE -ne 0) { Write-Fail "command failed: $Command" }
    }
}

# ---- step 1 — prereq checks ----------------------------------------------
Write-Step "step 1/7 — prereq checks"

if (-not $env:OPENAI_API_KEY) { Write-Fail "OPENAI_API_KEY env var must be set (do NOT pass via CLI)" }

foreach ($cmd in @("gcloud", "terraform", "curl")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Fail "$cmd not found in PATH"
    }
}

$activeAccount = (gcloud config get-value account 2>$null)
if (-not $activeAccount) { Write-Fail "gcloud not authenticated; run 'gcloud auth login'" }
$activeProject = (gcloud config get-value project 2>$null)
if ($activeProject -ne $ProjectId) {
    Write-Warn "active gcloud project '$activeProject' != -ProjectId '$ProjectId'"
    Write-Warn "set with: gcloud config set project $ProjectId"
}

Write-Step "  account=$activeAccount  project=$ProjectId  region=$Region"

if (-not $Confirm -and -not $DryRun) {
    Write-Warn "no -Confirm or -DryRun — stopping after prereq check"
    Write-Warn "re-run with -DryRun to preview, or -Confirm to apply"
    exit 0
}

# ---- step 2 — OPENAI_API_KEY secret --------------------------------------
Write-Step "step 2/7 — OPENAI_API_KEY in Secret Manager"

$secretExists = $false
gcloud secrets describe OPENAI_API_KEY --project=$ProjectId 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $secretExists = $true }

if ($secretExists) {
    Write-Step "  secret exists — adding new version"
} else {
    Write-Step "  secret missing — creating with replication=automatic"
    Invoke-Step "gcloud secrets create OPENAI_API_KEY --project=$ProjectId --replication-policy=automatic"
}

# Запись через stdin, чтобы не светить ключ в args (просматриваются в audit log).
if ($DryRun) {
    Write-Host "[dry-run] echo `$env:OPENAI_API_KEY | gcloud secrets versions add OPENAI_API_KEY --project=$ProjectId --data-file=-"
} else {
    $env:OPENAI_API_KEY | gcloud secrets versions add OPENAI_API_KEY --project=$ProjectId --data-file=-
    if ($LASTEXITCODE -ne 0) { Write-Fail "failed to add secret version" }
}

# ---- step 3 — terraform apply --------------------------------------------
Write-Step "step 3/7 — terraform apply environments/staging"

$tfDir = "infrastructure/terraform/environments/staging"
if (-not (Test-Path $tfDir)) { Write-Fail "terraform dir not found: $tfDir (run from repo root)" }

Invoke-Step "terraform -chdir=$tfDir init -input=false"
if ($DryRun) {
    Invoke-Step "terraform -chdir=$tfDir plan -input=false -target=google_storage_bucket.audio_sessions"
} else {
    Invoke-Step "terraform -chdir=$tfDir apply -input=false -auto-approve -target=google_storage_bucket.audio_sessions"
}

# ---- step 4 — bind secret to Cloud Run -----------------------------------
Write-Step "step 4/7 — wire OPENAI_API_KEY into Cloud Run revision"

Invoke-Step "gcloud run services update $ServiceName --region=$Region --project=$ProjectId --update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest"

# ---- step 5 — alembic reminder -------------------------------------------
Write-Step "step 5/7 — DB migration reminder"

@"
  [ACTION REQUIRED] alembic migration runs against staging DB outside this script.
  Connect via the AlloyDB Omni VM (private IP) using IAM auth, then:
    `$env:DATABASE_URL = '<staging-dsn>'
    uv run alembic upgrade head
  Confirm migration 0030 applied:
    uv run alembic current
"@ | Write-Host

# ---- step 6 — trigger new revision ---------------------------------------
Write-Step "step 6/7 — trigger new parser-service revision"

Invoke-Step "gcloud run services update $ServiceName --region=$Region --project=$ProjectId --update-labels=phase=10-9a"

# ---- step 7 — smoke ------------------------------------------------------
Write-Step "step 7/7 — smoke /healthz on $ServiceName"

if ($DryRun) {
    Invoke-Step "gcloud run services describe $ServiceName --region=$Region --project=$ProjectId --format='value(status.url)'"
} else {
    $parserUrl = (gcloud run services describe $ServiceName --region=$Region --project=$ProjectId --format='value(status.url)')
    if (-not $parserUrl) { Write-Fail "could not resolve $ServiceName URL" }
    Write-Step "  GET $parserUrl/healthz"
    try {
        $resp = Invoke-WebRequest -Uri "$parserUrl/healthz" -TimeoutSec 30 -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            Write-Step "  /healthz OK"
        } else {
            Write-Fail "  /healthz returned HTTP $($resp.StatusCode)"
        }
    } catch {
        Write-Fail "  /healthz failed — check 'gcloud run services logs read $ServiceName --region=$Region'"
    }
}

Write-Step "done. Next: run e2e demo rehearsal — see docs/runbooks/demo-rehearsal-2026-05-06.md"
