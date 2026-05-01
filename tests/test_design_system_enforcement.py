"""DS-2 enforcement tests — ADR-0067 §«Enforcement».

Двухсторонние gating-тесты:

- **Negative path (violations caught):** копируем подготовленный
  ``*.tsx.fixture`` (хранится с .fixture-суффиксом, чтобы сами hooks не
  подхватили его в кодовой базе) в tmp-dir под `apps/web/src/__planted__/`,
  вызываем hook напрямую и assert'им, что он падает с exit 1 + ожидаемые
  имена / темы упомянуты в stderr/stdout.

- **Positive path (clean main passes):** прогоняем hook на разрешённом
  файле без нарушений — ожидаем exit 0.

Hooks тестируются как Python modules (импорт + ``main(argv)``), а не через
subprocess — так быстрее (~ms против ~100ms на Windows) и нет проблемы с
закодировкой консоли.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "design_system_enforcement"

# Импортируем hooks как модули. Добавляем scripts/ в sys.path внутри
# fixture'ы, чтобы тест-collection не зависела от import-time.
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def lucide_hook():
    import check_lucide_allowlist

    return check_lucide_allowlist


@pytest.fixture
def anti_patterns_hook():
    import check_design_anti_patterns

    return check_design_anti_patterns


def _stage_fixture(tmp_path: Path, fixture_name: str, dest_relpath: str) -> Path:
    """Копирует .fixture файл в tmp-структуру под scope-prefix'ом hook'а."""
    src = FIXTURES_DIR / fixture_name
    dest = tmp_path / dest_relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest


# ---------------------------------------------------------------------------
# Lucide allowlist hook
# ---------------------------------------------------------------------------


def test_lucide_hook_passes_on_allowlisted_imports(lucide_hook, tmp_path, capsys, monkeypatch):
    """Allowlisted imports (Loader2, X, Chevron*, Grip*, More*) не падают."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "apps" / "web" / "src" / "components" / "allowlisted.tsx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        'import { ChevronDown, X, Loader2 } from "lucide-react";\n'
        "export function Ok() { return <ChevronDown />; }\n",
        encoding="utf-8",
    )
    rc = lucide_hook.main(["check_lucide_allowlist.py", str(src)])
    assert rc == 0


def test_lucide_hook_fails_on_forbidden_named_imports(
    lucide_hook,
    tmp_path,
    capsys,
    monkeypatch,
):
    """`Tree`, `Mail` и подобные content-glyphs ловятся; `Loader2` allow'ится."""
    monkeypatch.chdir(tmp_path)
    dest = _stage_fixture(
        tmp_path,
        "lucide_violation.tsx.fixture",
        "apps/web/src/components/planted-lucide.tsx",
    )
    rc = lucide_hook.main(["check_lucide_allowlist.py", str(dest)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Tree" in out
    assert "Mail" in out
    # Loader2 — allowlisted; не должен попадать в violations.
    assert "'Loader2'" not in out


def test_lucide_hook_skips_test_fixtures(lucide_hook, tmp_path, capsys, monkeypatch):
    """Файлы под __tests__/ и __fixtures__/ игнорируются — иначе planted
    нарушения в собственных тестах ломают live-репо."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "apps" / "web" / "src" / "__fixtures__" / "x.tsx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text('import { Tree } from "lucide-react";\n', encoding="utf-8")
    rc = lucide_hook.main(["check_lucide_allowlist.py", str(src)])
    assert rc == 0


# ---------------------------------------------------------------------------
# Anti-patterns hook
# ---------------------------------------------------------------------------


def test_anti_patterns_hook_passes_on_clean_file(
    anti_patterns_hook,
    tmp_path,
    capsys,
    monkeypatch,
):
    """Чистая копия (без emoji / fluff / dark-mode / heavy shadow) не падает."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "apps" / "web" / "src" / "components" / "ok.tsx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        'import * as React from "react";\n'
        "export function Ok() {\n"
        '  return <div className="shadow-sm">Evidence-based genealogy.</div>;\n'
        "}\n",
        encoding="utf-8",
    )
    rc = anti_patterns_hook.main(["check_design_anti_patterns.py", str(src)])
    assert rc == 0


def test_anti_patterns_hook_catches_emoji_fluff_shadow_dark(
    anti_patterns_hook,
    tmp_path,
    capsys,
    monkeypatch,
):
    """Planted-файл содержит все 5 категорий нарушений (A, C, D, E)."""
    monkeypatch.chdir(tmp_path)
    dest = _stage_fixture(
        tmp_path,
        "anti_patterns_violation.tsx.fixture",
        "apps/web/src/components/planted-anti-patterns.tsx",
    )
    rc = anti_patterns_hook.main(["check_design_anti_patterns.py", str(dest)])
    out = capsys.readouterr().out
    assert rc == 1
    # A — emoji
    assert "emoji" in out
    # C — fluff
    assert "fluff" in out.lower()
    # D — heavy shadow
    assert "shadow-2xl" in out or "heavy shadow" in out
    # E — dark mode
    assert "dark" in out.lower()


def test_anti_patterns_hook_allows_sex_unicode_symbols(
    anti_patterns_hook,
    tmp_path,
    capsys,
    monkeypatch,
):
    """Sex-symbols ♂ / ♀ / ⚧ — единственный README §iconography carve-out."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "apps" / "web" / "src" / "components" / "pedigree-test.tsx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        'import * as React from "react";\nexport const SEX_GLYPH = { M: "♂", F: "♀", X: "⚧" };\n',
        encoding="utf-8",
    )
    rc = anti_patterns_hook.main(["check_design_anti_patterns.py", str(src)])
    assert rc == 0


def test_anti_patterns_hook_catches_exclamation_in_jsx(
    anti_patterns_hook,
    tmp_path,
    capsys,
    monkeypatch,
):
    """B — exclamation marks: `<p>Welcome aboard!</p>` ловится."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "apps" / "web" / "src" / "components" / "shouty.tsx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        "export function Shouty() {\n  return <p>Welcome aboard! Redirecting now</p>;\n}\n",
        encoding="utf-8",
    )
    rc = anti_patterns_hook.main(["check_design_anti_patterns.py", str(src)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "exclamation" in out.lower()


def test_anti_patterns_hook_catches_exclamation_in_messages_json(
    anti_patterns_hook,
    tmp_path,
    capsys,
    monkeypatch,
):
    """B — exclamation marks: i18n value `"foo": "Hi!"` ловится."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "apps" / "web" / "messages" / "en.json"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text('{\n  "greeting": "Hello there!"\n}\n', encoding="utf-8")
    rc = anti_patterns_hook.main(["check_design_anti_patterns.py", str(src)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "exclamation" in out.lower()


# ---------------------------------------------------------------------------
# Live codebase regression guard — running with --all must succeed on main.
# ---------------------------------------------------------------------------


def test_live_codebase_passes_lucide_hook(lucide_hook, capsys):
    """``check_lucide_allowlist.py --all`` зелёный на текущем main."""
    rc = lucide_hook.main(["check_lucide_allowlist.py", "--all"])
    out = capsys.readouterr().out
    assert rc == 0, f"lucide hook unexpectedly failed on main: {out}"


def test_live_codebase_passes_anti_patterns_hook(anti_patterns_hook, capsys):
    """``check_design_anti_patterns.py --all`` зелёный на текущем main."""
    rc = anti_patterns_hook.main(["check_design_anti_patterns.py", "--all"])
    out = capsys.readouterr().out
    assert rc == 0, f"anti-patterns hook unexpectedly failed on main: {out}"
