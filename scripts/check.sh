#!/usr/bin/env bash
# Local mirror of `.github/workflows/ci.yml` job `lint-and-test` step commands.
# Run before `git push` — what passes here passes CI.
# Parity is enforced by tests/test_ci_parity.py.

set -euo pipefail

uv run ruff check .
uv run ruff format --check .
uv run pytest --cov --cov-report=xml --cov-report=term
