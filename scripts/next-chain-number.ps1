<#
.SYNOPSIS
    Returns next free alembic or ADR number considering main + all active worktrees.
.DESCRIPTION
    Prevents collisions when multiple agents work in parallel. Reads origin/main
    for current head, scans all worktrees for untracked + locally-committed files,
    returns the next safe number to claim.
.EXAMPLE
    pwsh scripts/next-chain-number.ps1 -Type alembic
    # Next free alembic: 0036
    #   down_revision    = 0035
    #   Claimed in worktrees: 0033, 0034, 0035

    pwsh scripts/next-chain-number.ps1 -Type adr -Json
    # {"NextNumber": "0072", "Claimed": ["0070", "0071"]}
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('alembic', 'adr')][string]$Type,
    [string]$RepoRoot = "F:\Projects\TreeGen",
    [string]$WorktreeRoot = "F:\Projects\TreeGen-wt",
    [switch]$Json
)

$ErrorActionPreference = 'Stop'

$config = @{
    alembic = @{
        Path    = 'infrastructure/alembic/versions'
        Pattern = '\d{4}_\d{2}_\d{2}_(\d{4})-'
    }
    adr = @{
        Path    = 'docs/adr'
        Pattern = '^(\d{4})-'
    }
}[$Type]

function Get-NumbersFromPath($wtPath, $relPath, $pattern, $sourceMode) {
    Push-Location $wtPath -ErrorAction SilentlyContinue
    if (-not $?) { return @() }

    $numbers = @()
    try {
        if ($sourceMode -eq 'main') {
            git fetch origin --quiet 2>$null
            $files = git ls-tree -r origin/main --name-only 2>$null `
                | Where-Object { $_ -like "$relPath/*" }
        }
        elseif ($sourceMode -eq 'untracked') {
            $files = git status --porcelain 2>$null `
                | Where-Object { $_ -match "^\?\? $relPath/" } `
                | ForEach-Object { ($_ -replace '^\?\? ', '').Trim() }
        }
        elseif ($sourceMode -eq 'localcommit') {
            $files = git log "origin/main..HEAD" --name-only --pretty=format: 2>$null `
                | Where-Object { $_ -like "$relPath/*" }
        }

        foreach ($f in $files) {
            $name = Split-Path $f -Leaf
            if ($name -match $pattern) {
                $numbers += [int]$Matches[1]
            }
        }
    } finally {
        Pop-Location
    }
    return $numbers
}

# 1. Main head
$mainNumbers = Get-NumbersFromPath -wtPath $RepoRoot -relPath $config.Path `
    -pattern $config.Pattern -sourceMode 'main'
$mainTop = if ($mainNumbers.Count) { [int]($mainNumbers | Measure-Object -Maximum).Maximum } else { 0 }

# 2. Scan all worktrees
# Git on Windows emits forward-slash paths in `worktree list --porcelain`,
# so normalise both sides of the prefix match to forward slashes.
$wtRootNormalised = $WorktreeRoot -replace '\\', '/'
$claimed = @()
Push-Location $RepoRoot
$worktrees = git worktree list --porcelain `
    | Select-String "^worktree " `
    | ForEach-Object { $_.Line.Substring(9) } `
    | Where-Object { ($_ -replace '\\', '/') -like "$wtRootNormalised*" }
Pop-Location

foreach ($wt in $worktrees) {
    if (-not (Test-Path $wt)) { continue }
    $claimed += Get-NumbersFromPath -wtPath $wt -relPath $config.Path `
        -pattern $config.Pattern -sourceMode 'untracked'
    $claimed += Get-NumbersFromPath -wtPath $wt -relPath $config.Path `
        -pattern $config.Pattern -sourceMode 'localcommit'
}

# 3. Compute next free
# Защитный flatten: PS может вернуть скаляр для одноэлементного pipeline.
$claimed = @($claimed | Sort-Object -Unique)
$allClaimed = @($mainTop) + ($mainNumbers) + $claimed | Sort-Object -Unique
$next = $mainTop + 1
while ($next -in $allClaimed) { $next++ }

# 4. Output
$result = [PSCustomObject]@{
    Type               = $Type
    NextNumber         = "{0:D4}" -f $next
    DownRevision       = "{0:D4}" -f $mainTop
    MainHead           = "{0:D4}" -f $mainTop
    ClaimedInWorktrees = @($claimed | ForEach-Object { "{0:D4}" -f $_ })
}

if ($Json) {
    $result | ConvertTo-Json -Compress
} else {
    Write-Host "Next free $Type`: $($result.NextNumber)" -ForegroundColor Green
    if ($Type -eq 'alembic') {
        Write-Host "  down_revision    = $($result.DownRevision)"
    }
    Write-Host "  Main head        = $($result.MainHead)"
    if ($result.ClaimedInWorktrees.Count) {
        Write-Host "  Claimed in worktrees: $($result.ClaimedInWorktrees -join ', ')" -ForegroundColor Yellow
    }
}
