"""ADR-0067 §«Enforcement» Decision C — mechanical anti-pattern detection.

Pre-commit / CI hook, который ловит механически-детектируемые нарушения
DS-1 voice + visual-language rules:

A. Emoji в user-facing strings (.tsx / .ts / messages/*.json) — DS-1
   §Anti-patterns: «No emoji. Status uses dot + label.»
B. Восклицательные знаки в JSX-text node'ах и i18n-строках — DS-1
   §Voice: «no exclamation marks anywhere».
C. Marketing-fluff phrases (amazing / powerful / unlock / transform your /
   discover your / incredible) — DS-1 §Voice anti-patterns.
D. Heavy shadow utilities (`shadow-2xl`, `shadow-inner`) — DS-1 §Visual:
   «No drop shadows for emphasis. No glow.»
E. Dark-mode artifacts в продуктовых .tsx / .ts файлах
   (`prefers-color-scheme: dark`, `dark:bg-…`, `dark:text-…`,
   `[data-theme="dark"]`, `.dark` selectors). DS-1 уже catch'ит drift в
   tokens/SKILL/README через ``test_design_system_consistency.py``;
   здесь — extension на src/ tree.

Decision (vs. ESLint): см. ``check_lucide_allowlist.py`` docstring —
biome 1.9.4 ограничения + прецедент ``check_i18n_strings.py``.

Hook читает PRE-COMMIT-предоставленный список файлов из argv. Скоупы
per-категория: см. ``_CATEGORIES``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Префиксы in-scope для разных categories. messages/*.json подключаются
# к A (emoji) и B (exclamations) — voice rules касаются user-facing copy.
_SRC_PREFIXES = ("apps/web/src/", "apps/landing/src/")
_MESSAGES_PREFIXES = ("apps/web/messages/", "apps/landing/messages/")

# Test fixture / __tests__ / __fixtures__ skip — там планты-нарушения
# тестируют сами hooks.
_SKIP_MARKERS = ("__tests__/", "__fixtures__/", "/test/", "/tests/")

# === A. EMOJI ===============================================================
# Unicode ranges, покрывающие большинство pictographic emoji + symbol blocks.
# Не purely-pictographic-only — 2600..27BF включает arrows / dingbats; для
# UI-копии все они равнозначно forbidden.
_EMOJI = re.compile(
    "[\U0001f000-\U0001ffff"  # Misc symbols + pictographs + emoji blocks
    "☀-⛿"  # Misc symbols (☀ ⚠ ⭐ etc.)
    "✀-➿"  # Dingbats (✅ ✨ etc.)
    "]"
)
# README §Iconography carve-out: «No unicode symbols as icons. The single
# exception is ♂ / ♀ / ⚧ for sex on person cards (matches the product's
# pedigree-tree.tsx).»
_EMOJI_ALLOWLIST = frozenset({"♂", "♀", "⚧"})  # ♂ ♀ ⚧

# === B. EXCLAMATION MARKS in copy ==========================================
# Ловим:
# - JSX text node с `!` (>… ! …<)
# - i18n string values содержащие `!` (внутри "..." между `:` и `,` / `}`)
# False positives: `!=`, `!==`, `!(...)`, `!isLoading`, `// not!` — для них
# нужно run на JSX text-only / JSON values. Используем heuristic.
_JSX_EXCL = re.compile(r">[^<{]*?[A-Za-z][^<{]*?![\s<]")
# Любая строковая литерала в JSON, которая содержит букву + `!` где `!`
# не в конце ID-like токена.
_JSON_STRING_EXCL = re.compile(r'"\s*:\s*"[^"]*[A-Za-z][^"]*![^"]*"')

# === C. MARKETING FLUFF ====================================================
_FLUFF_TERMS = (
    "amazing",
    "powerful",
    "unlock",
    "transform your",
    "discover your",
    "incredible",
)
_FLUFF = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _FLUFF_TERMS) + r")\b",
    re.IGNORECASE,
)

# === D. HEAVY SHADOWS ======================================================
# Tailwind utility classes inside JSX className / clsx / cn() calls.
# `shadow-2xl` и `shadow-inner` — anti-pattern (excessive emphasis); design
# system допускает только `shadow-sm` / `shadow-md` / `shadow-lg` (последний
# для modals only) + custom `shadow-[var(--shadow-card)]` тоже ок.
_HEAVY_SHADOW = re.compile(r"\bshadow-(?:2xl|inner)\b")

# === E. DARK MODE ARTIFACTS ================================================
_DARK_PATTERNS = (
    re.compile(r"prefers-color-scheme\s*:\s*dark"),
    # Tailwind dark: variant classes
    re.compile(r"\bdark:(?:bg-|text-|border-|ring-|shadow-|hover:|focus:|placeholder:)"),
    # CSS attribute selector
    re.compile(r'\[data-theme\s*=\s*"?dark"?\]'),
    # `next-themes` import — после DS-1 не должно быть в репо
    re.compile(r"""from\s+["']next-themes["']"""),
)


def _is_src_scope(path: Path) -> bool:
    posix = path.as_posix()
    if not any(p in posix for p in _SRC_PREFIXES):
        return False
    if any(m in posix for m in _SKIP_MARKERS):
        return False
    return path.suffix in {".ts", ".tsx", ".css"}


def _is_messages_scope(path: Path) -> bool:
    posix = path.as_posix()
    return any(p in posix for p in _MESSAGES_PREFIXES) and path.suffix == ".json"


def _check_emoji(content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        for match in _EMOJI.finditer(line):
            char = match.group(0)
            if char in _EMOJI_ALLOWLIST:
                continue
            findings.append((line_no, f"emoji char '{char}' — DS-1 §voice forbids emoji"))
    return findings


def _check_exclamations(path: Path, content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    is_json = path.suffix == ".json"
    pattern = _JSON_STRING_EXCL if is_json else _JSX_EXCL
    for line_no, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            stripped = line.strip()
            findings.append(
                (
                    line_no,
                    f"exclamation mark in user-facing copy — DS-1 §voice "
                    f"forbids `!`. line: {stripped[:120]}",
                )
            )
    return findings


def _check_fluff(content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        match = _FLUFF.search(line)
        if match:
            findings.append(
                (
                    line_no,
                    f"marketing-fluff term: '{match.group(0)}' — DS-1 §voice "
                    f"forbids hype. Prefer evidence-first phrasing",
                )
            )
    return findings


def _check_heavy_shadow(content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        match = _HEAVY_SHADOW.search(line)
        if match:
            findings.append(
                (
                    line_no,
                    f"heavy shadow utility '{match.group(0)}' — DS-1 §visual "
                    f"forbids drop-shadow as emphasis. Use shadow-sm/md/lg",
                )
            )
    return findings


def _check_dark_mode(content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        for pattern in _DARK_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(
                    (
                        line_no,
                        f"dark-mode artifact '{match.group(0)}' — ADR-0067 commits "
                        f"to light-mode-only V1",
                    )
                )
                break
    return findings


def _discover_all_files() -> list[Path]:
    """Self-discover all in-scope files via `git ls-files`.

    Used for ``--all`` standalone invocation (CI / check.sh / check.ps1).
    """
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "apps/web/src",
                "apps/landing/src",
                "apps/web/messages",
                "apps/landing/messages",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [Path(line) for line in result.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    # На Windows cp1252-консоли стандартный stdout падает на любом
    # non-ASCII символе (а мы их и ищем — emoji + кириллица в i18n).
    # Перенастраиваем на UTF-8 с заменой непечатных через ?.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = argv[1:]
    files = _discover_all_files() if args == ["--all"] else [Path(arg) for arg in args]

    failed = False
    for path in files:
        in_src = _is_src_scope(path)
        in_messages = _is_messages_scope(path)
        if not (in_src or in_messages):
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        findings: list[tuple[int, str]] = []
        # A — emoji: и в src, и в messages
        findings.extend(_check_emoji(content))
        # B — exclamations: и в src (JSX text), и в messages (JSON values).
        # `path` нужен — JSON-файлы парсятся другим regex'ом.
        findings.extend(_check_exclamations(path, content))
        # C — fluff: src + messages (both can carry marketing copy)
        if in_src or in_messages:
            findings.extend(_check_fluff(content))
        # D — heavy shadow: только src (className strings)
        if in_src:
            findings.extend(_check_heavy_shadow(content))
        # E — dark mode: только src (компоненты + стили; SKILL/README cover'ит
        # `test_design_system_consistency.py`)
        if in_src:
            findings.extend(_check_dark_mode(content))

        if findings:
            failed = True
            print(f"\n{path.as_posix()}:")
            for line_no, message in findings:
                print(f"  line {line_no}: {message}")

    if failed:
        print(
            "\nADR-0067 §«Enforcement» Decision C — design-system anti-patterns "
            "are mechanically rejected. See SKILL.md / README.md §Anti-patterns "
            "and `preview/brand-icon-style-spec.html` for the complete list."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
