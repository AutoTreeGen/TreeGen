#!/usr/bin/env bash
# Local mirror of `.github/workflows/ci.yml` job `lint-and-test` step commands.
# Run before `git push` — what passes here passes CI.
# Parity is enforced by tests/test_ci_parity.py.
#
# TODO Phase 4.2: тест парности (test_ci_parity.py) учитывает только `uv run`
# команды; pnpm-проверки frontend ниже добавлены локально, в CI их пока нет.
# При вводе frontend-job в ci.yml — расширить парсер парности на pnpm.

set -euo pipefail

uv run ruff check .
uv run ruff format --check .
uv run pytest --cov --cov-report=xml --cov-report=term

# ---- Frontend (Phase 4.1+) — пока локально, не в CI ------------------------
pnpm -r typecheck
pnpm -r lint
