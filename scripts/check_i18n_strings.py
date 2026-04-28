"""Phase 4.13 — flag raw English JSX text in authenticated app pages.

ADR-0037 §«Lint enforcement» — выбран custom-regex hook вместо ESLint
плагина (мы на biome) или TS-плагина (overhead). Hook работает на
файлах из allowlist'а:

    apps/web/src/app/dashboard/**.tsx
    apps/web/src/app/persons/**.tsx
    apps/web/src/app/dna/**.tsx
    apps/web/src/app/sources/**.tsx
    apps/web/src/app/hypotheses/**.tsx
    apps/web/src/app/familysearch/**.tsx
    apps/web/src/app/settings/**.tsx
    apps/web/src/app/trees/**.tsx
    apps/web/src/components/**.tsx (НО marketing-only оставляем allow'ом)

Исключения (см. _SKIP_REGEX):
    - JSX text внутри ``<title>`` / ``<style>`` / ``<script>`` / ``<code>``
      и ``<pre>`` — не локализуем.
    - Идентификаторы, error-codes (camelCase / snake_case без пробелов).
    - URL'ы и e-mail'ы.
    - Строки длиной ≤ 2 символа (символы, эмодзи, нумерация).
    - Цифры и пунктуация.

Hook читает PRE-COMMIT-предоставленный список файлов из argv. Если
никакой файл не попал в allowlist — exit 0 без output.

Не идеален (regex-based), но дешёвый: ловит самое частое — забытые
``<h1>Some text</h1>``. Пропускает: динамические выражения, шаблонные
литералы, JSX-аттрибуты с raw текстом (``placeholder="..."``). На
эти случаи есть locale-rendering vitest, который ловит missing-key
fallback'и — двойная защита.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Authenticated routes — те, что должны быть на 100% i18n. Public marketing
# pages (page.tsx, demo/, pricing/, onboarding/) уже сделаны в Phase 4.12.
_AUTHENTICATED_PATTERNS = (
    "apps/web/src/app/dashboard/",
    "apps/web/src/app/persons/",
    "apps/web/src/app/dna/",
    "apps/web/src/app/sources/",
    "apps/web/src/app/hypotheses/",
    "apps/web/src/app/familysearch/",
    "apps/web/src/app/settings/",
    "apps/web/src/app/trees/",
)

# Раз shared-components сидят в /components — добавим только те,
# что точно используются в auth-страницах (marketing components оставляем
# allow'нутыми до Phase 4.13b).
_SHARED_COMPONENTS = (
    "apps/web/src/components/site-header.tsx",
    "apps/web/src/components/notification-bell.tsx",
    "apps/web/src/components/error-message.tsx",
)

# Соответствует JSX text node: > X < где X — английский текст (3+ chars,
# хотя бы 2 алфавитных, без переменных {} и без import-style идентификаторов).
# multiline=False, потому что построчно работаем — простые случаи на одной строке.
_JSX_TEXT = re.compile(
    r">\s*([A-Z][A-Za-z][A-Za-z .,!?'’—\-]{3,})\s*<",
)

# Skipping markers: JSX-внутри элементов где английский ОК.
_SKIP_TAGS = re.compile(
    r"<(title|style|script|code|pre)\b",
)

# Comments / imports / TODO ...
_COMMENT_LINE = re.compile(r"^\s*(//|\*|/\*)")

# Allow-list для конкретных доменных терминов, которые остаются английскими
# даже в русском интерфейсе (генеалогические термины, технические
# идентификаторы).
_DOMAIN_TERMS_ALLOW = {
    "GEDCOM",
    "DNA",
    "RNA",
    "API",
    "SVG",
    "CSV",
    "JSON",
    "QUAY",
    "BIRT",
    "DEAT",
    "MARR",
    "OAuth",
    "FamilySearch",
    "AutoTreeGen",
    "MyHeritage",
    "Ancestry",
    "GEDmatch",
    "Phase",
}


def _is_in_scope(path: Path) -> bool:
    """Проверяет, попадает ли файл в allowlist auth-страниц."""
    posix = path.as_posix()
    if any(p in posix for p in _AUTHENTICATED_PATTERNS):
        return True
    return any(posix.endswith(s) for s in _SHARED_COMPONENTS)


def _check_file(path: Path) -> list[tuple[int, str]]:
    """Возвращает список (line_no, raw_text) подозрительных строк."""
    findings: list[tuple[int, str]] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    inside_skip_tag = False
    for line_no, line in enumerate(content.splitlines(), start=1):
        if _COMMENT_LINE.search(line):
            continue
        if _SKIP_TAGS.search(line):
            inside_skip_tag = True
        if "</title>" in line or "</style>" in line or "</script>" in line:
            inside_skip_tag = False
            continue
        if inside_skip_tag:
            continue
        for match in _JSX_TEXT.finditer(line):
            raw = match.group(1).strip()
            # Эвристика: строка должна содержать минимум 1 пробел или быть
            # целым предложением, иначе это скорее всего вариант кнопки / id.
            if " " not in raw:
                continue
            if raw.strip() in _DOMAIN_TERMS_ALLOW:
                continue
            # Если строка содержит только цифры/пунктуацию/символы — пропуск.
            if not re.search(r"[A-Za-z]{3,}", raw):
                continue
            findings.append((line_no, raw))
    return findings


def main(argv: list[str]) -> int:
    files = [Path(arg) for arg in argv[1:] if arg.endswith(".tsx")]
    target_files = [f for f in files if _is_in_scope(f)]
    if not target_files:
        return 0

    failed = False
    for path in target_files:
        findings = _check_file(path)
        if not findings:
            continue
        failed = True
        print(f"\n{path.as_posix()}:")
        for line_no, text in findings:
            print(f"  line {line_no}: raw English JSX text: '{text}'")
            print("    → wrap with `useTranslations(...)` and add the key to messages/{en,ru}.json")
    if failed:
        print(
            "\nADR-0037: every authenticated-route JSX text node must come from "
            "next-intl. Move strings to `apps/web/messages/{en,ru}.json`."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
