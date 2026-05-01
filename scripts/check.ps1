#!/usr/bin/env pwsh
# Local mirror of `.github/workflows/ci.yml` job `lint-and-test` step commands.
# Run before `git push` — what passes here passes CI.
# Parity is enforced by tests/test_ci_parity.py.
#
# TODO Phase 4.2: тест парности (test_ci_parity.py) учитывает только `uv run`
# команды; pnpm-проверки frontend ниже добавлены локально, в CI их пока нет.
# При вводе frontend-job в ci.yml — расширить парсер парности на pnpm.

$ErrorActionPreference = "Stop"

uv run ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run python scripts/check_lucide_allowlist.py --all
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run python scripts/check_design_anti_patterns.py --all
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run pytest --cov --cov-report=xml --cov-report=term
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# ---- Frontend (Phase 4.1+) — пока локально, не в CI ------------------------
pnpm -r typecheck
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

pnpm -r lint
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
