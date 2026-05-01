"""ADR-0067 §«Enforcement» Decision A — lucide-react import allowlist.

Pre-commit / CI hook, который форсит per-name allowlist для импортов из
``lucide-react``: разрешены только tiny inline UI affordances (chevrons,
close, drag handles, more-handles) плюс Loader2 для async indicators.

Все остальные lucide иконки = content-iconography → должны идти через
3D-modern brand SVG (см. ``preview/brand-iconography.html``).

Decision B (см. ADR-0067 addendum): hook написан на Python вместо ESLint
introduction, потому что:
- biome 1.9.4 ``noRestrictedImports`` поддерживает только path-level
  restriction, не per-name (``importNames`` пришли в biome 2.x).
- Введение ESLint ради одного правила = parallel-linter overhead
  (две конфигурации, два ignore-list'а, конфликты formatter'ов).
- Прецедент в репо: ``scripts/check_i18n_strings.py`` (Phase 4.13).

Hook читает PRE-COMMIT-предоставленный список файлов из argv. Если ни
один не подпадает под scope (apps/{web,landing}/src/**/*.tsx и .ts) —
exit 0 без output.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Финальный allowlist — Q1 owner decision 2026-05-01:
# - 4× Chevron* — нативные dropdown / accordion / disclosure carets
# - X — close / dismiss
# - GripVertical / GripHorizontal — drag handles
# - MoreHorizontal / MoreVertical — overflow menus
# - Loader2 — ONLY for inline button spinners and async indicators
#   (UI affordance per DS-1 §iconography, не content)
ALLOWED_LUCIDE_NAMES: frozenset[str] = frozenset(
    {
        "ChevronDown",
        "ChevronUp",
        "ChevronLeft",
        "ChevronRight",
        "X",
        "GripVertical",
        "GripHorizontal",
        "MoreHorizontal",
        "MoreVertical",
        "Loader2",
    }
)

# Scope: только apps/{web,landing}/src — фронтенд-код. Skip __tests__,
# чтобы планты-фикстуры (см. tests/test_design_system_enforcement.py) не
# ломали лайв-репо.
_SCOPE_PREFIXES = (
    "apps/web/src/",
    "apps/landing/src/",
)
_TEST_FIXTURES = ("__tests__/", "__fixtures__/")

# Match: import { Foo, Bar as Baz } from "lucide-react" (single- или
# multi-line). Захватываем всё внутри `{...}` блока и парсим вручную —
# multiline блоки распространены в waitlist-form-style импортах.
_LUCIDE_IMPORT = re.compile(
    r"""
    import \s+
    (?:type \s+)?              # `import type {...}` тоже ловим
    \{ (?P<names> [^}]* ) \}
    \s* from \s*
    ["'] lucide-react ["']
    """,
    re.VERBOSE | re.DOTALL,
)

# Match: import Foo from "lucide-react" — default-import формы у lucide
# нет, но если кто-то напишет — ловим как violation.
_LUCIDE_DEFAULT = re.compile(
    r"""
    import \s+ (?P<name> [A-Za-z_][\w]* )
    \s+ from \s*
    ["'] lucide-react ["']
    """,
    re.VERBOSE,
)


def _is_in_scope(path: Path) -> bool:
    posix = path.as_posix()
    if not any(prefix in posix for prefix in _SCOPE_PREFIXES):
        return False
    if any(marker in posix for marker in _TEST_FIXTURES):
        return False
    return path.suffix in {".ts", ".tsx"}


def _parse_named_imports(block: str) -> list[str]:
    """Достаём имена из `{Foo, Bar as Baz, type Qux}` блока."""
    names: list[str] = []
    for raw in block.split(","):
        token = raw.strip()
        if not token:
            continue
        # Strip inline `type ` prefix (TS type imports внутри объединённого
        # `import { type X, Y }` блока).
        if token.startswith("type "):
            token = token[5:].strip()
        # `Foo as Bar` — берём оригинальное имя (Foo).
        original = token.split(" as ")[0].strip()
        if original:
            names.append(original)
    return names


def _check_file(path: Path) -> list[tuple[int, str]]:
    """Возвращает [(line_no, message)] нарушений."""
    findings: list[tuple[int, str]] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    for match in _LUCIDE_IMPORT.finditer(content):
        block = match.group("names")
        names = _parse_named_imports(block)
        line_no = content.count("\n", 0, match.start()) + 1
        for name in names:
            if name not in ALLOWED_LUCIDE_NAMES:
                findings.append(
                    (
                        line_no,
                        f"forbidden lucide-react import: '{name}' — "
                        f"swap to 3D-modern brand SVG (preview/brand-iconography.html) "
                        f"or use canonical inline-stroke SVG for tiny affordances",
                    )
                )

    for match in _LUCIDE_DEFAULT.finditer(content):
        line_no = content.count("\n", 0, match.start()) + 1
        findings.append(
            (
                line_no,
                f"default-import from 'lucide-react' is not supported "
                f"and would bypass the allowlist: '{match.group('name')}'",
            )
        )

    return findings


def _discover_all_files() -> list[Path]:
    """Self-discover все ts/tsx файлы в scope через `git ls-files`.

    Используется при запуске с ``--all`` (CI / scripts/check.sh / check.ps1):
    pre-commit hook передаёт изменённые файлы, а standalone-вызов должен
    сканировать всю кодовую базу.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "apps/web/src", "apps/landing/src"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [Path(line) for line in result.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = argv[1:]
    files = _discover_all_files() if args == ["--all"] else [Path(arg) for arg in args]

    targets = [f for f in files if _is_in_scope(f)]
    if not targets:
        return 0

    failed = False
    for path in targets:
        findings = _check_file(path)
        if not findings:
            continue
        failed = True
        print(f"\n{path.as_posix()}:")
        for line_no, message in findings:
            print(f"  line {line_no}: {message}")

    if failed:
        allowed = ", ".join(sorted(ALLOWED_LUCIDE_NAMES))
        print(
            "\nADR-0067 §«Enforcement» Decision A — lucide-react import "
            "allowlist is exhaustive. Permitted names: " + allowed + "."
        )
        print(
            "Brand-facing iconography MUST use the 3D-modern SVG language "
            "documented in `preview/brand-icon-style-spec.html` (canonical "
            "set: `preview/brand-iconography.html`)."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
