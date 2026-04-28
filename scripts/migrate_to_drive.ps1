#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Перенос проекта TreeGen с одного диска на другой через robocopy.

.DESCRIPTION
    Зеркалирует D:\Projects\TreeGen в указанную целевую папку с разумными
    исключениями кэшей и зависимостей. Источник не трогает (delete source —
    отдельный ручной шаг после верификации).

    По умолчанию ИСКЛЮЧАЕТ:
      - .venv, node_modules, apps/web/.next  (пересобираются на новом диске)
      - .pytest_cache, .ruff_cache, .mypy_cache, __pycache__
      - .coverage, coverage.xml
      - .git/index.lock (stale lock cleanup)

    Это сделано осознанно: pnpm node_modules содержит hardlinks в global store
    на D:\, простая копия даст битый граф. uv .venv — Python.exe со ссылками
    на интерпретатор. Безопаснее пересобрать через `uv sync` + `pnpm install`
    после переезда (1–3 минуты).

.PARAMETER Source
    Путь-источник. По умолчанию D:\Projects\TreeGen.

.PARAMETER Destination
    Путь-цель, например F:\Projects\TreeGen.

.PARAMETER CopyDeps
    Скопировать .venv / node_modules / .next тоже. Не рекомендуется — см. выше.

.PARAMETER DryRun
    Не писать ничего, только показать что будет скопировано (robocopy /L).

.PARAMETER LogFile
    Куда писать robocopy лог. По умолчанию рядом со скриптом.

.EXAMPLE
    pwsh scripts/migrate_to_drive.ps1 -Destination F:\Projects\TreeGen -DryRun

.EXAMPLE
    pwsh scripts/migrate_to_drive.ps1 -Destination F:\Projects\TreeGen
#>

param(
    [string]$Source = "D:\Projects\TreeGen",
    [Parameter(Mandatory = $true)][string]$Destination,
    [switch]$CopyDeps,
    [switch]$DryRun,
    [string]$LogFile = "$PSScriptRoot\migrate_to_drive.log"
)

$ErrorActionPreference = "Stop"

function Write-Section($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

function Fail($text) {
    Write-Host "FAIL: $text" -ForegroundColor Red
    exit 1
}

# ---- 1. Pre-flight ----------------------------------------------------------
Write-Section "Pre-flight"

if (-not (Test-Path $Source)) { Fail "Source not found: $Source" }
$Source = (Resolve-Path $Source).Path

# Запрет копировать в подпапку источника — иначе бесконечная рекурсия
$srcNorm = $Source.TrimEnd('\').ToLower()
$dstNorm = $Destination.TrimEnd('\').ToLower()
if ($dstNorm.StartsWith($srcNorm + '\') -or $dstNorm -eq $srcNorm) {
    Fail "Destination is inside Source. Aborting."
}

$dstRoot = Split-Path -Parent $Destination
if (-not (Test-Path $dstRoot)) {
    Write-Host "Creating destination root: $dstRoot"
    if (-not $DryRun) { New-Item -ItemType Directory -Path $dstRoot -Force | Out-Null }
}

# Свободное место на целевом диске
$dstDrive = (Split-Path -Qualifier $Destination).TrimEnd(':') + ':'
$drive = Get-PSDrive -Name $dstDrive.TrimEnd(':') -ErrorAction SilentlyContinue
if (-not $drive) { Fail "Destination drive $dstDrive not found." }
$freeGB = [math]::Round($drive.Free / 1GB, 2)
Write-Host "Destination drive ${dstDrive} free: ${freeGB} GB"

# Грубая оценка размера источника
Write-Host "Sizing source (this may take a few seconds)..."
$srcSizeGB = [math]::Round((Get-ChildItem -Path $Source -Recurse -Force -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum / 1GB, 2)
Write-Host "Source size (raw, with deps): ${srcSizeGB} GB"

if ($freeGB -lt ($srcSizeGB + 2)) {
    Write-Host "WARNING: Free space < source + 2 GB headroom." -ForegroundColor Yellow
}

# ---- 2. Stale locks ---------------------------------------------------------
Write-Section "Stale lock cleanup"

$indexLock = Join-Path $Source ".git\index.lock"
if (Test-Path $indexLock) {
    $age = (Get-Date) - (Get-Item $indexLock).LastWriteTime
    Write-Host "Found .git/index.lock (age: $($age.TotalMinutes.ToString('F1')) min)"
    if (-not $DryRun) {
        Remove-Item $indexLock -Force
        Write-Host "Removed."
    }
}

# ---- 3. Robocopy ------------------------------------------------------------
Write-Section "Robocopy"

# Каталоги, которые робокопи НЕ должен заходить (если не -CopyDeps)
$xdParts = @(
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "__pycache__", ".coverage_html"
)
if (-not $CopyDeps) {
    $xdParts += @(".venv", "node_modules")
    # .next — Next.js build, всегда исключаем
    $xdParts += @("$Source\apps\web\.next")
}

# Файлы-исключения
$xfParts = @(
    ".coverage", "coverage.xml", ".coverage.*"
)

$flags = @(
    "/MIR",         # mirror (reset target)
    "/COPY:DAT",    # data, attributes, timestamps (БЕЗ ACL — на разных дисках разные ACL)
    "/DCOPY:DAT",   # то же для директорий
    "/SJ", "/SL",   # junctions/symlinks как есть
    "/MT:16",       # multithread
    "/R:2", "/W:5", # retry 2x, wait 5s
    "/NP",          # no progress per-file
    "/NFL", "/NDL", # no file/dir list (логи короче)
    "/TEE",         # stdout + log
    "/UNILOG+:$LogFile"
)
if ($DryRun) { $flags += "/L" }

$xdArgs = @()
foreach ($d in $xdParts) { $xdArgs += @("/XD", $d) }
$xfArgs = @()
foreach ($f in $xfParts) { $xfArgs += @("/XF", $f) }

# Заодно очистить лог
if (Test-Path $LogFile) { Remove-Item $LogFile -Force }

$rcArgs = @($Source, $Destination) + $flags + $xdArgs + $xfArgs

Write-Host "robocopy command:"
Write-Host "  robocopy $($rcArgs -join ' ')" -ForegroundColor DarkGray
Write-Host ""

if ($DryRun) {
    Write-Host "DRY RUN — no files will be copied." -ForegroundColor Yellow
}

& robocopy @rcArgs
$rc = $LASTEXITCODE

# Robocopy: 0–7 = success (различные оттенки), 8+ = ошибка
if ($rc -ge 8) {
    Fail "Robocopy failed with code $rc. See log: $LogFile"
}
Write-Host "Robocopy finished (exit $rc — success). Log: $LogFile"

# ---- 4. Post-migration sanity ----------------------------------------------
Write-Section "Post-migration sanity"

if ($DryRun) {
    Write-Host "Dry run — skipping sanity checks." -ForegroundColor Yellow
    exit 0
}

# git fsck в новом расположении
Push-Location $Destination
try {
    Write-Host "Running 'git status' in destination..."
    & git status -sb
    if ($LASTEXITCODE -ne 0) { Write-Host "git status returned non-zero." -ForegroundColor Yellow }

    Write-Host ""
    Write-Host "Running 'git fsck --no-dangling'..."
    & git fsck --no-dangling 2>&1 | Select-Object -First 10
}
finally {
    Pop-Location
}

# ---- 5. Что осталось сделать ----------------------------------------------
Write-Section "Manual follow-up"

@"
Скрипт скопировал проект, но НЕ удалял источник.

Дальше:

1. Перепривяжи окружения на новом диске:
     cd $Destination
     uv sync
     pnpm install

2. Обнови абсолютные пути:
     - .claude/settings.local.json — заменить D:/Projects/TreeGen на $($Destination -replace '\\','/')
     - (опционально) CLAUDE.md, ROADMAP.md, docs/agent-briefs/* — упоминания D:\Projects\TreeGen
       можно оставить как исторические или переименовать массово

3. Проверь .env:
     - DATABASE_URL, MINIO endpoints — обычно localhost, путь не важен
     - GEDCOM_TEST_CORPUS — если корпус GED тоже переезжает, обнови

4. Перенаправь IDE:
     - VS Code: открыть $Destination как папку
     - PyCharm: File → Open → $Destination, удалить старый проект из recent

5. Smoke-test:
     pwsh scripts/check.ps1
     uv run pytest -m "not slow and not integration"

6. Только когда всё зелёное — удали источник:
     Remove-Item -Recurse -Force $Source

7. Docker volumes (Postgres/Redis/MinIO) живут в Docker daemon, НЕ в папке проекта.
   Перенос диска их не затронул. docker compose up -d должен поднять то же состояние.
"@ | Write-Host

Write-Host ""
Write-Host "Migration COPY phase complete." -ForegroundColor Green
