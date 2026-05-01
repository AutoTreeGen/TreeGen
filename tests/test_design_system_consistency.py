"""Структурные тесты Design System v1 (Phase DS-1 / ADR-0067).

Проверяют, что spec-fixes из брифа DS-1 не дрейфуют:
- SKILL.md фиксирует PT Serif (а не Manrope) как display-семейство.
- README.md палитра icons перечисляет все девять b3* gradients.
- Light-mode-only: нет ``prefers-color-scheme: dark``, нет ``[data-theme="dark"]``,
  нет фразы «dark mode» / «dark-mode» в SKILL.md, README.md, design-токенах.
- Дубликат ``brand-iconography-3d-modern.html`` удалён.
- Все 24 иконки в ``preview/brand-iconography.html`` имеют ``cy="85"`` для
  ground-эллипса (унифицировано в DS-1 fix #7).

Эти тесты не требуют сети / Postgres / Docker — pure-text grep на репо.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Канонические локации файлов после DS-1 интеграции.
SKILL_MD = REPO_ROOT / ".claude" / "skills" / "design-system" / "SKILL.md"
README_MD = REPO_ROOT / "README.md"
TOKENS_CSS = REPO_ROOT / "colors_and_type.css"
ICONOGRAPHY_HTML = REPO_ROOT / "preview" / "brand-iconography.html"
DEAD_DUPLICATE = REPO_ROOT / "preview" / "brand-iconography-3d-modern.html"

# Apps' duplicates (DRY violation acknowledged для v1, ADR-0067):
APP_TOKEN_FILES = [
    REPO_ROOT / "apps" / "web" / "src" / "styles" / "design-system.css",
    REPO_ROOT / "apps" / "landing" / "src" / "styles" / "design-system.css",
]

# Девять gradient-id'шек, которые должна перечислять палитра в README.md.
B3_GRADIENTS = [
    "b3Pink",
    "b3Cyan",
    "b3Mint",
    "b3Gold",
    "b3Coral",
    "b3Plum",
    "b3Cream",
    "b3Wood",
    "b3Paper",
]

# Паттерны, которые НЕ должны встречаться в light-mode-only V1.
DARK_PATTERNS = [
    re.compile(r"dark[ -]mode", re.IGNORECASE),
    re.compile(r"prefers-color-scheme"),
    re.compile(r'\[data-theme\s*=\s*"?dark"?\]'),
]


# ---------------------------------------------------------------------------
# Fix #1 — fonts
# ---------------------------------------------------------------------------


def test_skill_md_exists() -> None:
    """SKILL.md размещён в ``.claude/skills/design-system/`` (DS-1 §placement)."""
    assert SKILL_MD.is_file(), f"missing {SKILL_MD}"


def test_skill_md_uses_pt_serif_not_manrope() -> None:
    """SKILL.md должен называть PT Serif как display, не Manrope (DS-1 fix #1)."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "PT Serif" in text, "SKILL.md must reference PT Serif"
    assert "Manrope" not in text, "SKILL.md still references Manrope — fix #1 not applied"


# ---------------------------------------------------------------------------
# Fix #5 — palette completeness
# ---------------------------------------------------------------------------


def test_readme_palette_lists_all_nine_b3_gradients() -> None:
    """README.md должен явно перечислять все 9 b3* gradients (включая b3Paper)."""
    text = README_MD.read_text(encoding="utf-8")
    missing = [g for g in B3_GRADIENTS if g not in text]
    assert not missing, f"README.md palette missing: {missing}"


# ---------------------------------------------------------------------------
# Fix #4 — duplicate file removed
# ---------------------------------------------------------------------------


def test_brand_iconography_3d_modern_duplicate_absent() -> None:
    """Файл-дубликат brand-iconography-3d-modern.html должен отсутствовать (DS-1 fix #4)."""
    assert not DEAD_DUPLICATE.exists(), (
        f"{DEAD_DUPLICATE.name} still present — duplicate должен быть удалён"
    )


# ---------------------------------------------------------------------------
# Fix #7 — unified cy=85 ground ellipse
# ---------------------------------------------------------------------------


def test_iconography_ground_ellipses_use_cy_85() -> None:
    """Все 24 иконки используют ``cy="85"`` для ground-эллипса (DS-1 fix #7)."""
    text = ICONOGRAPHY_HTML.read_text(encoding="utf-8")
    ground_lines = [line for line in text.splitlines() if 'class="ground"' in line]
    assert len(ground_lines) == 24, f"expected 24 ground ellipses, found {len(ground_lines)}"
    bad = [line.strip() for line in ground_lines if not re.search(r'cy="85"', line)]
    assert not bad, f"ground ellipse(s) with non-85 cy: {bad}"


# ---------------------------------------------------------------------------
# Light-mode only commitment (ADR-0067)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        SKILL_MD,
        README_MD,
        TOKENS_CSS,
        *APP_TOKEN_FILES,
    ],
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_dark_mode_artifacts(path: Path) -> None:
    """Ни SKILL.md, ни README.md, ни design-токены не содержат dark-mode паттернов.

    Light-mode-only V1 — owner decision 2026-05-01 (ADR-0067).
    """
    if not path.is_file():
        pytest.fail(f"expected DS-1 file missing: {path}")
    text = path.read_text(encoding="utf-8")
    for pattern in DARK_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{path.relative_to(REPO_ROOT)} contains forbidden pattern "
            f"{pattern.pattern!r}: {match.group(0)!r} — ADR-0067 violation"
        )


# ---------------------------------------------------------------------------
# CSS imports wired in apps' globals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "globals_path",
    [
        REPO_ROOT / "apps" / "web" / "src" / "app" / "globals.css",
        REPO_ROOT / "apps" / "landing" / "src" / "app" / "globals.css",
    ],
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_globals_imports_design_system(globals_path: Path) -> None:
    """Каждое приложение импортирует общие токены через @import (DS-1 §wiring)."""
    text = globals_path.read_text(encoding="utf-8")
    assert "design-system.css" in text, (
        f"{globals_path.relative_to(REPO_ROOT)} must @import design-system.css"
    )
