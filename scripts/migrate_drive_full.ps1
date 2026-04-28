#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Полный перенос пользовательских данных с диска SourceDrive на DestDrive.

.DESCRIPTION
    Сканирует корень SourceDrive, классифицирует top-level папки и применяет
    разные robocopy-стратегии. Источник не трогает (D: остаётся как backup).

    Стратегии:
      - SKIP_SYS         — системные папки ($RECYCLE.BIN, System Volume Information)
      - SKIP_REINSTALL   — приложения, которые лучше переустановить (ClaudeCode)
      - DEEP_PROJECT     — папка проектов, обходим вложенно с исключением кэшей
      - DEEP_PNPM_STORE  — pnpm global store, копия + post-config note
      - PLAIN            — обычные данные (DNA, Images, Personal, GIT, .claude)

    Перенос НЕ удаляет источник. После верификации:
        Remove-Item -Recurse -Force D:\<folder>

.PARAMETER SourceDrive
    Буква исходного диска (по умолчанию D:).

.PARAMETER DestDrive
    Буква целевого диска (по умолчанию F:).

.PARAMETER DryRun
    Только показать что будет, не копировать.

.PARAMETER LogDir
    Куда писать robocopy-логи (по умолчанию рядом со скриптом).

.PARAMETER OnlyFolder
    Перенести только одну top-level папку (для отладки/повтора).

.EXAMPLE
    pwsh scripts/migrate_drive_full.ps1 -DryRun

.EXAMPLE
    pwsh scripts/migrate_drive_full.ps1

.EXAMPLE
    pwsh scripts/migrate_drive_full.ps1 -OnlyFolder Personal
#>

param(
    [string]$SourceDrive = "D:",
    [string]$DestDrive = "F:",
    [switch]$DryRun,
    [string]$LogDir = "$PSScriptRoot\migrate-logs",
    [string]$OnlyFolder
)

$ErrorActionPreference = "Stop"

# ---- Конфигурация: имена системных и специальных папок --------------------
$SYS_FOLDERS = @(
    '$RECYCLE.BIN', 'System Volume Information', 'RECYCLER',
    'Recovery', 'Config.Msi', 'Documents and Settings'
)

# Папки, которые лучше переустановить чем копировать
$REINSTALL_FOLDERS = @('ClaudeCode')

# Кэши/зависимости проектов — исключаем при копировании любого подпроекта
$PROJECT_CACHES = @(
    '.venv', 'venv', 'env',
    'node_modules',
    '.next', '.nuxt', '.svelte-kit',
    '.pytest_cache', '.ruff_cache', '.mypy_cache',
    '__pycache__', '.coverage_html',
    'target',                    # Rust
    '.gradle', 'build',          # Java/Gradle (build conflicts with Python build)
    '.terraform'
)

$PROJECT_CACHE_FILES = @('.coverage', 'coverage.xml', '.coverage.*')

# ---- Helpers --------------------------------------------------------------
function Write-Section($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

function Format-Bytes($bytes) {
    if ($bytes -ge 1GB) { return ("{0:N2} GB" -f ($bytes / 1GB)) }
    if ($bytes -ge 1MB) { return ("{0:N1} MB" -f ($bytes / 1MB)) }
    if ($bytes -ge 1KB) { return ("{0:N0} KB" -f ($bytes / 1KB)) }
    return "$bytes B"
}

function Get-FolderSize($path) {
    try {
        $size = (Get-ChildItem $path -Recurse -Force -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
        return [int64]($size ?? 0)
    } catch { return 0 }
}

function Classify-Folder($name) {
    if ($SYS_FOLDERS -contains $name) { return 'SKIP_SYS' }
    if ($REINSTALL_FOLDERS -contains $name) { return 'SKIP_REINSTALL' }
    if ($name -eq 'Projects')     { return 'DEEP_PROJECT' }
    if ($name -eq '.pnpm-store')  { return 'DEEP_PNPM_STORE' }
    return 'PLAIN'
}

function Invoke-Robocopy {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$LogFile,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )

    $flags = @(
        '/MIR', '/COPY:DAT', '/DCOPY:DAT',
        '/SJ', '/SL',
        '/MT:16', '/R:2', '/W:5',
        '/NP', '/NFL', '/NDL',
        "/UNILOG+:$LogFile"
    )
    if ($DryRun) { $flags += '/L' }

    $args = @($Source, $Destination) + $flags
    foreach ($d in $ExcludeDirs)  { $args += @('/XD', $d) }
    foreach ($f in $ExcludeFiles) { $args += @('/XF', $f) }

    Write-Host "  robocopy $($Source) -> $($Destination)" -ForegroundColor DarkGray
    & robocopy @args | Out-Null
    return $LASTEXITCODE
}

function Migrate-PlainFolder($srcPath, $dstPath, $logFile) {
    $rc = Invoke-Robocopy -Source $srcPath -Destination $dstPath -LogFile $logFile
    return $rc
}

function Migrate-ProjectsFolder($srcRoot, $dstRoot, $logDir) {
    # Сначала копируем файлы корня Projects/ (но не папки) — на случай README и пр.
    if (-not (Test-Path $dstRoot)) {
        if (-not $DryRun) { New-Item -ItemType Directory -Path $dstRoot -Force | Out-Null }
    }

    $subdirs = Get-ChildItem $srcRoot -Directory -Force -ErrorAction SilentlyContinue
    Write-Host "  Found $($subdirs.Count) subprojects in $srcRoot"

    foreach ($sub in $subdirs) {
        $rawSize = Get-FolderSize $sub.FullName
        $hasNode = Test-Path (Join-Path $sub.FullName 'node_modules')
        $hasVenv = Test-Path (Join-Path $sub.FullName '.venv')
        $hasNext = Test-Path (Join-Path $sub.FullName 'apps\web\.next')
        $hasGit  = Test-Path (Join-Path $sub.FullName '.git')

        $tags = @()
        if ($hasGit)  { $tags += 'git' }
        if ($hasNode) { $tags += 'node_modules' }
        if ($hasVenv) { $tags += '.venv' }
        if ($hasNext) { $tags += '.next' }

        $tagsStr = if ($tags.Count) { " [$($tags -join ', ')]" } else { '' }
        Write-Host "  - $($sub.Name) ($(Format-Bytes $rawSize))$tagsStr"

        $subDst = Join-Path $dstRoot $sub.Name
        $subLog = Join-Path $logDir "Projects-$($sub.Name).log"

        # Исключаем кэши/зависимости — пересоберём после переноса
        $rc = Invoke-Robocopy -Source $sub.FullName -Destination $subDst `
            -LogFile $subLog `
            -ExcludeDirs $PROJECT_CACHES `
            -ExcludeFiles $PROJECT_CACHE_FILES

        if ($rc -ge 8) {
            Write-Host "    FAIL: robocopy exit $rc — see $subLog" -ForegroundColor Red
        }
    }
}

function Migrate-PnpmStore($src, $dst, $log) {
    Write-Host "  Note: pnpm hardlinks reduce store size on D:; copy will inflate to physical size on F:."
    $rc = Invoke-Robocopy -Source $src -Destination $dst -LogFile $log
    return $rc
}

# ---- Pre-flight ------------------------------------------------------------
Write-Section "Pre-flight"

$SourceDrive = $SourceDrive.TrimEnd('\').TrimEnd(':') + ':'
$DestDrive   = $DestDrive.TrimEnd('\').TrimEnd(':') + ':'
$srcRoot = "$SourceDrive\"
$dstRoot = "$DestDrive\"

if (-not (Test-Path $srcRoot)) { Write-Host "Source drive $srcRoot not found." -ForegroundColor Red; exit 1 }
if (-not (Test-Path $dstRoot)) { Write-Host "Destination drive $dstRoot not found." -ForegroundColor Red; exit 1 }

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Свободное место
$dstPS = Get-PSDrive -Name $DestDrive.TrimEnd(':')
$srcPS = Get-PSDrive -Name $SourceDrive.TrimEnd(':')
$srcUsed = $srcPS.Used
$dstFree = $dstPS.Free
Write-Host ("Source $SourceDrive used: {0}" -f (Format-Bytes $srcUsed))
Write-Host ("Dest   $DestDrive free: {0}" -f (Format-Bytes $dstFree))

if ($dstFree -lt $srcUsed) {
    Write-Host "WARNING: Free space on dest < used on source. Migration with -CopyDeps would fail." -ForegroundColor Yellow
    Write-Host "Скрипт исключает кэши/зависимости — реальный объём будет ниже." -ForegroundColor Yellow
}

# ---- Inventory ------------------------------------------------------------
Write-Section "Inventory $srcRoot"

$items = Get-ChildItem $srcRoot -Force -ErrorAction SilentlyContinue
$plan = @()
foreach ($item in $items) {
    if ($OnlyFolder -and $item.Name -ne $OnlyFolder) { continue }

    $kind = if ($item.PSIsContainer) { 'DIR' } else { 'FILE' }
    if ($kind -eq 'DIR') {
        $strategy = Classify-Folder $item.Name
    } else {
        $strategy = 'PLAIN_FILE'
    }
    $plan += [pscustomobject]@{
        Name = $item.Name; Kind = $kind; Strategy = $strategy
        Source = $item.FullName
        Dest = Join-Path $dstRoot $item.Name
    }
}

$plan | Format-Table Name, Kind, Strategy -AutoSize | Out-String | Write-Host

# ---- Confirm ---------------------------------------------------------------
if (-not $DryRun) {
    Write-Host "Press ENTER to start migration, or Ctrl+C to abort." -ForegroundColor Yellow
    [void](Read-Host)
}

# ---- Execute ---------------------------------------------------------------
Write-Section ($DryRun ? 'DRY RUN' : 'Migrating')

$startTime = Get-Date
$skipped = @()

foreach ($entry in $plan) {
    Write-Host ""
    Write-Host "--- [$($entry.Strategy)] $($entry.Name) ---" -ForegroundColor Magenta

    switch ($entry.Strategy) {
        'SKIP_SYS' {
            Write-Host "  Skipping system folder."
            $skipped += $entry.Name
        }
        'SKIP_REINSTALL' {
            Write-Host "  Skipping — reinstall on $DestDrive recommended."
            $skipped += "$($entry.Name) (reinstall)"
        }
        'PLAIN_FILE' {
            $log = Join-Path $LogDir "_root.log"
            if ($DryRun) {
                Write-Host "  [DRY] Would copy file to $($entry.Dest)"
            } else {
                Copy-Item $entry.Source $entry.Dest -Force
                Write-Host "  Copied."
            }
        }
        'PLAIN' {
            $log = Join-Path $LogDir "$($entry.Name).log"
            $rc = Migrate-PlainFolder $entry.Source $entry.Dest $log
            if ($rc -ge 8) { Write-Host "  FAIL exit $rc — see $log" -ForegroundColor Red }
            else { Write-Host "  OK (robocopy exit $rc)" }
        }
        'DEEP_PROJECT' {
            Migrate-ProjectsFolder $entry.Source $entry.Dest $LogDir
        }
        'DEEP_PNPM_STORE' {
            $log = Join-Path $LogDir "pnpm-store.log"
            $rc = Migrate-PnpmStore $entry.Source $entry.Dest $log
            if ($rc -ge 8) { Write-Host "  FAIL exit $rc — see $log" -ForegroundColor Red }
            else { Write-Host "  OK (robocopy exit $rc)" }
        }
    }
}

$elapsed = (Get-Date) - $startTime

# ---- Post-migration follow-up ---------------------------------------------
Write-Section "Done"
Write-Host ("Elapsed: {0:hh\:mm\:ss}" -f $elapsed)
Write-Host "Logs in: $LogDir"

if ($skipped) {
    Write-Host ""
    Write-Host "Skipped:" -ForegroundColor Yellow
    $skipped | ForEach-Object { Write-Host "  - $_" }
}

@"

=== Что сделать руками после ===

1. pnpm — переключить store на новый диск:
     pnpm config set store-dir $DestDrive\.pnpm-store

   Потом в каждом JS-проекте, которому нужны зависимости:
     cd <project>
     Remove-Item -Recurse -Force node_modules
     pnpm install

2. Python venv'ы — пересоздать (на D: они исключены при копировании):
     cd <project>
     uv sync     # или: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt

3. Next.js .next — пересоберётся при первом запуске:
     pnpm dev    # или pnpm build

4. ClaudeCode — переустановка с https://claude.ai/download (текущая папка
   $SourceDrive\ClaudeCode НЕ скопирована).

5. Конкретно TreeGen:
     cd $DestDrive\Projects\TreeGen
     # обновить .claude/settings.local.json — D:/Projects/TreeGen → $DestDrive/Projects/TreeGen
     uv sync
     pnpm install
     pwsh scripts/check.ps1

6. IDE (PyCharm/VS Code/Cursor) — переоткрыть проекты с новых путей,
   удалить старые из recent.

7. Системные ярлыки/Path:
     - Проверь PATH: `[Environment]::GetEnvironmentVariable('PATH','User')`
     - Проверь ярлыки на рабочем столе и в Start Menu — могут указывать на $SourceDrive\

8. Когда всё работает с $DestDrive\ — можно очистить $SourceDrive (D: остаётся
   как backup пока не убедишься).
"@ | Write-Host
