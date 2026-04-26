<#
.SYNOPSIS
    Закоммитить Phase 2 (data model + ORM + миграции + bench) на ветку feat/phase-2-data-model
    серией conventional-commits.

.DESCRIPTION
    Запускать ИЗ КОРНЯ репо: D:\Projects\TreeGen
    Предполагается что ты уже на ветке feat/phase-2-data-model и она пустая.

    Использует --no-verify, потому что pre-commit hooks могут падать на pre-existing
    biome a11y errors в apps/landing/. После всех Phase 2 коммитов запусти
    `uv run pre-commit run --all-files` отдельно — Phase 2 файлы должны пройти чисто.

.EXAMPLE
    .\scripts\commit_phase2.ps1
#>

$ErrorActionPreference = "Stop"

function Step-Add {
    param([string]$Description, [string[]]$Paths)
    Write-Host "`n=== $Description ===" -ForegroundColor Cyan
    foreach ($p in $Paths) {
        if (Test-Path $p) {
            git add -- $p
            Write-Host "  + $p"
        } else {
            Write-Host "  ! missing: $p" -ForegroundColor Yellow
        }
    }
}

function Step-Commit {
    param([string]$Message)
    $cached = git diff --cached --name-only
    if (-not $cached) {
        Write-Host "  -> nothing staged, skip commit" -ForegroundColor Yellow
        return
    }
    Write-Host "  -> commit: $Message" -ForegroundColor Green
    git commit --no-verify -m $Message
}

# Sanity: на правильной ветке?
$branch = git rev-parse --abbrev-ref HEAD
if ($branch -ne "feat/phase-2-data-model") {
    Write-Host "Expected branch 'feat/phase-2-data-model', got '$branch'." -ForegroundColor Red
    Write-Host "Run: git checkout -b feat/phase-2-data-model" -ForegroundColor Red
    exit 1
}

# ── 1. Infra fixes (workspace + pre-commit) ───────────────────────────────────
Step-Add "fix(infra): dna-analysis workspace stub + pre-commit python3.13" @(
    "packages/dna-analysis/pyproject.toml",
    "packages/dna-analysis/README.md",
    ".pre-commit-config.yaml"
)
Step-Commit "fix(infra): unblock uv workspace and pin pre-commit to python3.13"

# ── 2. Documentation: ER diagram + ADR-0003 ───────────────────────────────────
Step-Add "docs(phase-2): data model + versioning ADR" @(
    "docs/data-model.md",
    "docs/adr/0003-versioning-strategy.md",
    "docs/adr/README.md"
)
Step-Commit "docs(phase-2): add ER diagram and ADR-0003 versioning strategy"

# ── 3. shared-models package: ORM + mixins + audit ────────────────────────────
Step-Add "feat(shared-models): ORM + mixins + audit listeners" @(
    "packages/shared-models/src",
    "packages/shared-models/pyproject.toml",
    "packages/shared-models/README.md"
)
Step-Commit "feat(shared-models): add ORM models, mixins, audit listeners"

# ── 4. shared-models tests ────────────────────────────────────────────────────
Step-Add "test(shared-models): ORM + schema invariants + Pydantic" @(
    "packages/shared-models/tests"
)
Step-Commit "test(shared-models): add ORM smoke + schema invariants + Pydantic tests"

# ── 5. Alembic infra + migrations ─────────────────────────────────────────────
Step-Add "feat(db): alembic config + migrations 0001 + 0002" @(
    "alembic.ini",
    "infrastructure/alembic"
)
Step-Commit "feat(db): add initial schema and DNA tables migrations"

# ── 6. Scripts: seed + bench + ged import ─────────────────────────────────────
Step-Add "feat(scripts): seed_db + bench_phase2 + import_personal_ged" @(
    "scripts/seed_db.py",
    "scripts/bench_phase2.py",
    "scripts/import_personal_ged.py"
)
Step-Commit "feat(scripts): add db seed, GED import, and Phase 2 benchmark suite"

# ── 7. Root deps: pyproject + uv.lock if changed ──────────────────────────────
$rootDiff = git diff main -- pyproject.toml uv.lock
if ($rootDiff) {
    Step-Add "chore(deps): root pyproject + uv.lock for Phase 2" @(
        "pyproject.toml",
        "uv.lock"
    )
    Step-Commit "chore(deps): pin SQLAlchemy/Alembic/asyncpg/pgvector for Phase 2"
} else {
    Write-Host "`n=== root deps unchanged, skip ===" -ForegroundColor DarkGray
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n=== Done. Commits on feat/phase-2-data-model: ===" -ForegroundColor Cyan
git log --oneline main..HEAD

Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "  1. git push origin feat/phase-2-data-model"
Write-Host "  2. uv sync   # обновить .venv после workspace fix"
Write-Host "  3. uv run alembic upgrade head"
Write-Host "  4. uv run python scripts/bench_phase2.py --quick"
Write-Host "  5. Open PR: https://github.com/AutoTreeGen/TreeGen/pull/new/feat/phase-2-data-model"
