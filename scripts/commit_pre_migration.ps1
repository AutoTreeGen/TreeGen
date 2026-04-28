#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Закоммитить untracked artifacts перед/после переноса проекта на другой диск.

.DESCRIPTION
    Идемпотентный скрипт: можно запускать многократно, корректно обрабатывает
    застрявший index, pre-commit auto-fixes и stash-rollback.

    Поток для каждого коммита:
      1. git reset (очистить index)
      2. Прогнать pre-commit вручную на нужных файлах (применит auto-fixes)
      3. git add целевых файлов
      4. git commit (хуки прогонятся повторно — должны быть idempotent)

    Это обходит баг pre-commit где stash unstaged + hook auto-fix конфликтуют
    и hook fixes откатываются.
#>

param(
    [string]$Repo = (Resolve-Path "$PSScriptRoot\..").Path
)

$ErrorActionPreference = "Stop"

Push-Location $Repo
try {
    # 1. Stale lock
    $lock = ".git\index.lock"
    if (Test-Path $lock) {
        $age = ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes
        Write-Host "Removing stale $lock (age: $($age.ToString('F1')) min)"
        Remove-Item $lock -Force
    }

    # 2. Reset index — очистить все накопленные с прошлых неудачных попыток staged изменения
    Write-Host "Resetting index to HEAD..."
    git reset HEAD -- . | Out-Null

    $branch = git rev-parse --abbrev-ref HEAD
    Write-Host "Branch: $branch"

    # Helper: один коммит с pre-pass через pre-commit
    function Invoke-CleanCommit {
        param(
            [string[]]$Paths,
            [string]$Message,
            [string]$Label
        )
        Write-Host ""
        Write-Host "--- $Label ---" -ForegroundColor Cyan

        # Развернуть директорийные paths в списки реальных файлов (для pre-commit --files)
        $files = @()
        foreach ($p in $Paths) {
            if (Test-Path $p -PathType Container) {
                $files += (Get-ChildItem $p -Recurse -File).FullName |
                    ForEach-Object { Resolve-Path -Relative $_ }
            } elseif (Test-Path $p) {
                $files += $p
            }
        }
        # Нормализация слэшей
        $files = $files | ForEach-Object { $_ -replace '\\', '/' } | Sort-Object -Unique
        if (-not $files) {
            Write-Host "  Nothing to commit for $Label."
            return
        }

        # 1. Прогнать pre-commit вручную — auto-fixes применятся в working tree
        Write-Host "  Running pre-commit auto-fixes on $($files.Count) file(s)..."
        $hookArgs = @('--files') + $files
        & uv run pre-commit run @hookArgs 2>&1 | Out-Null
        # exit code здесь игнорируем — нам важно что auto-fixes применились в working tree

        # 2. Stage (после auto-fix)
        git add @Paths
        $cached = git diff --cached --name-only
        if (-not $cached) {
            Write-Host "  After auto-fix, no changes to commit for $Label."
            return
        }
        $count = ($cached | Measure-Object).Count
        Write-Host "  Committing $count staged file(s)..."

        # 3. Commit — хуки прогонятся ещё раз; должны быть no-op после первого pass
        git commit -m $Message
        if ($LASTEXITCODE -ne 0) {
            throw "$Label commit failed even after pre-pass. Run 'git status' to inspect."
        }
        Write-Host "  $Label committed." -ForegroundColor Green
    }

    # 3. Commit 1 — .gitignore (если ещё не закоммичен)
    Invoke-CleanCommit `
        -Paths @('.gitignore') `
        -Message "chore(gitignore): exclude .claude/scheduled_tasks.lock" `
        -Label ".gitignore"

    # 4. Commit 2 — agent briefs
    Invoke-CleanCommit `
        -Paths @('docs/agent-briefs') `
        -Message "docs(agent-briefs): add phase briefs (phases 1.x, 3.4-3.6, 4.3-4.9, 5.1-5.2, 6.1-6.2, 7.0-7.3, 8.0, 9.0)" `
        -Label "agent briefs"

    # 5. Commit 3 — migration scripts
    Invoke-CleanCommit `
        -Paths @(
            'scripts/migrate_to_drive.ps1',
            'scripts/commit_pre_migration.ps1',
            'scripts/migrate_drive_full.ps1'
        ) `
        -Message "chore(scripts): add drive migration helpers" `
        -Label "migration scripts"

    Write-Host ""
    Write-Host "=== git log -5 ===" -ForegroundColor Cyan
    git log --oneline -5
    Write-Host ""
    Write-Host "Done. Review with 'git log -p' before pushing." -ForegroundColor Green
}
finally {
    Pop-Location
}
