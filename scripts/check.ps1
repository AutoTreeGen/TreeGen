#!/usr/bin/env pwsh
# Local mirror of `.github/workflows/ci.yml` job `lint-and-test` step commands.
# Run before `git push` — what passes here passes CI.
# Parity is enforced by tests/test_ci_parity.py.

$ErrorActionPreference = "Stop"

uv run ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv run pytest --cov --cov-report=xml --cov-report=term
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
