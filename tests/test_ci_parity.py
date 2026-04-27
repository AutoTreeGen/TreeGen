"""Инвариант ADR-0008: scripts/check.* должен буквально совпадать с CI.

Парсит ``.github/workflows/ci.yml`` job ``lint-and-test`` и
``scripts/check.sh``, извлекает реальные shell-команды (отбрасывая тривиальные
шаги CI: checkout, setup-uv, setup-python, uv sync, codecov upload), и
проверяет, что множества команд совпадают.

Если CI workflow добавляет новый шаг — этот тест упадёт, пока такая же команда
не будет добавлена в check.sh / check.ps1 (и наоборот). Это та самая
«parity-инварианта», ради которой и существует ADR-0008.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_CHECK_SH = _REPO_ROOT / "scripts" / "check.sh"
_CHECK_PS1 = _REPO_ROOT / "scripts" / "check.ps1"

# Шаги CI, которые не воспроизводятся локально (загружают сторонние actions /
# запускают CI-инфраструктуру). Сравниваем по subcommand-токену uv: для них
# токена просто нет, поэтому фильтр работает по полной строке.
_TRIVIAL_RUN_PREFIXES = (
    "uv python install",
    "uv sync",
)


def _normalize(cmd: str) -> str:
    """Свернуть пробелы и continuation-backslashes к одной строке.

    Pre-commit ruff-format может разнести длинную команду на несколько строк
    через ``\\``; CI YAML обычно держит её одной строкой. Нормализуем оба.
    """
    collapsed = re.sub(r"\\\s*\n\s*", " ", cmd)
    return re.sub(r"\s+", " ", collapsed).strip()


def _ci_run_commands() -> set[str]:
    """Вытащить ``run:`` команды из job ``lint-and-test`` ci.yml."""
    data = yaml.safe_load(_CI_YAML.read_text(encoding="utf-8"))
    job = data["jobs"]["lint-and-test"]
    commands: set[str] = set()
    for step in job["steps"]:
        run = step.get("run")
        if not run:
            continue
        normalized = _normalize(run)
        if any(normalized.startswith(prefix) for prefix in _TRIVIAL_RUN_PREFIXES):
            continue
        commands.add(normalized)
    return commands


def _check_sh_commands() -> set[str]:
    """Вытащить uv-команды из scripts/check.sh."""
    commands: set[str] = set()
    for raw_line in _CHECK_SH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "set ")):
            continue
        normalized = _normalize(line)
        if normalized.startswith("uv run "):
            commands.add(normalized)
    return commands


def _check_ps1_commands() -> set[str]:
    """Вытащить uv-команды из scripts/check.ps1.

    Skip pwsh-shebang, ErrorActionPreference, и ``if ($LASTEXITCODE...)`` guards.
    """
    commands: set[str] = set()
    for raw_line in _CHECK_PS1.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "$ErrorActionPreference", "if ", "exit ")):
            continue
        normalized = _normalize(line)
        if normalized.startswith("uv run "):
            commands.add(normalized)
    return commands


def test_check_sh_matches_ci_commands() -> None:
    """check.sh выполняет тот же набор команд, что и CI ``lint-and-test``."""
    assert _check_sh_commands() == _ci_run_commands(), (
        "scripts/check.sh и .github/workflows/ci.yml разошлись. "
        "Обнови оба файла одновременно (см. ADR-0008)."
    )


def test_check_ps1_matches_ci_commands() -> None:
    """check.ps1 выполняет тот же набор команд, что и CI ``lint-and-test``."""
    assert _check_ps1_commands() == _ci_run_commands(), (
        "scripts/check.ps1 и .github/workflows/ci.yml разошлись. "
        "Обнови оба файла одновременно (см. ADR-0008)."
    )


def test_check_sh_and_ps1_have_same_commands() -> None:
    """Windows и Unix обёртки эквивалентны по набору шагов."""
    assert (
        _check_sh_commands() == _check_ps1_commands()
    ), "scripts/check.sh и scripts/check.ps1 разошлись."
