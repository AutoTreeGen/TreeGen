"""Archive-spelling variants generator (Phase 15.10 / ADR-0068).

Combines :mod:`patronymic` / :mod:`transliterate` / :mod:`daitch_mokotoff` /
:mod:`synonyms` в одну функцию ``generate_archive_variants(name)`` →
``set[str]``. Возвращаемый set содержит:

1. Оригинальный input.
2. Cross-script transliterations (Cyrillic ↔ Latin × {BGN, ISO 9, LoC};
   Hebrew → Latin × {BGN, LoC}; Latin → Hebrew best-guess для AJ-фамилий).
3. Diacritic fold + restore-candidates для Polish / German.
4. ICP-anchor synonyms (``data/icp_anchor_synonyms.json``).
5. DM phonetic codes с префиксом ``__DM__`` чтобы caller мог отделить
   spelling variants от phonetic-keys одной командой
   ``{v for v in variants if not v.startswith('__DM__')}``.

Empty set никогда не возвращается — минимум ``{name, "__DM__<code>"}``.
"""

from __future__ import annotations

from typing import Final, Literal

from entity_resolution.names.daitch_mokotoff import dm_soundex
from entity_resolution.names.synonyms import canonical_form, load_icp_synonyms
from entity_resolution.names.transliterate import Transliterator

SourceLang = Literal["ru", "uk", "by", "pl", "en", "he", "yi", "de", "auto"]


# Префикс DM-кодов в variants set'е. Caller'ы variants'а часто хотят
# spelling vs phonetic separately:
# ``spellings = {v for v in variants if not v.startswith(_DM_PREFIX)}``.
_DM_PREFIX: Final[str] = "__DM__"

# AJ-эвристика: surname-окончания, плюс membership в ICP-anchor table.
_JEWISH_SURNAME_SUFFIXES: Final[tuple[str, ...]] = (
    "man",
    "stein",
    "witz",
    "sky",
    "ski",
    "berg",
    "baum",
    "feld",
    "ovich",
    "evich",
    "ovitz",
    "evitz",
    "owitz",
    "ewicz",
    "owicz",
    "shitz",
    "blatt",
    "thal",
    "stadt",
    "burger",
)

# Cyrillic / Hebrew Unicode ranges (упрощённо, но покрывает 99% случаев).
_CYRILLIC_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x0400, 0x04FF),  # Cyrillic
    (0x0500, 0x052F),  # Cyrillic Supplement
    (0x2DE0, 0x2DFF),  # Cyrillic Extended-A
    (0xA640, 0xA69F),  # Cyrillic Extended-B
)
_HEBREW_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x0590, 0x05FF),  # Hebrew block
)


def _char_in_ranges(ch: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in ranges)


def _detect_script(name: str) -> SourceLang:
    """Эвристическое определение script'а по большинству символов.

    Возвращает один из ``cyrillic`` / ``hebrew`` / ``latin``-семейств
    (mapped в SourceLang ``ru`` / ``he`` / ``en`` соответственно). Для
    ``auto`` mode'а variants'а это и есть тот lookup, который определяет
    куда транслитерировать дальше.
    """
    cyr = sum(1 for ch in name if _char_in_ranges(ch, _CYRILLIC_RANGES))
    heb = sum(1 for ch in name if _char_in_ranges(ch, _HEBREW_RANGES))
    if cyr >= max(heb, 1):
        return "ru"
    if heb >= max(cyr, 1):
        return "he"
    return "en"


def _is_likely_jewish(name: str) -> bool:
    """Heuristic: AJ surname-окончание ИЛИ membership в ICP-anchor table.

    False-negatives ожидаемы (Cohen / Levy без suffix'а — но они в
    anchor-table) — поэтому второй disjunct.
    """
    lowered = name.lower()
    if any(lowered.endswith(s) for s in _JEWISH_SURNAME_SUFFIXES):
        return True
    return canonical_form(name) in load_icp_synonyms()


def generate_archive_variants(
    name: str,
    *,
    source_lang: SourceLang = "auto",
) -> set[str]:
    """Build archive-relevant spelling variants для fuzzy lookup'а.

    Args:
        name: Любая форма (English, Cyrillic, Hebrew, Polish-folded, ...).
            Пустой / whitespace-only → возвращается ``set`` с пустой
            строкой и пустым DM-кодом (caller'ы должны уметь это игнорить).
        source_lang: Hint про script. ``auto`` — детектим по содержимому.
            Конкретные коды (``ru``, ``he``, ...) сужают behavior; cross-
            script transliterations всё равно генерятся.

    Returns:
        ``set[str]`` с минимум 1 элементом. Префикс ``__DM__`` перед
        DM-кодами (см. модульный docstring).
    """
    if not name or not name.strip():
        return {name}

    out: set[str] = {name}
    tl = Transliterator()

    detected = source_lang if source_lang != "auto" else _detect_script(name)

    # 1. Cross-script transliteration.
    if detected in ("ru", "uk", "by"):
        for std in ("bgn", "iso9", "loc"):
            out.add(tl.to_latin(name, source_script="cyrillic", standard=std))  # type: ignore[arg-type]
    elif detected in ("en", "pl", "de"):
        out.add(tl.to_cyrillic(name, source_script="latin"))
        if _is_likely_jewish(name):
            out.add(tl.to_hebrew(name, source_script="latin"))
    elif detected == "he":
        for std in ("bgn", "loc"):
            out.add(tl.to_latin(name, source_script="hebrew", standard=std))  # type: ignore[arg-type]
        out.add(tl.to_cyrillic(name, source_script="hebrew"))
    elif detected == "yi":
        for std in ("bgn", "loc"):
            out.add(tl.to_latin(name, source_script="yiddish", standard=std))  # type: ignore[arg-type]

    # 2. Diacritic folds (apply unconditionally — cheap, и часто помогает,
    # даже если name выглядит как Cyrillic — это no-op для не-Latin chars).
    out.update(tl.normalize_diacritics(name, lang="pl"))
    out.update(tl.normalize_diacritics(name, lang="de"))

    # 3. ICP-anchor synonyms.
    synonyms = load_icp_synonyms().get(canonical_form(name))
    if synonyms is not None:
        out.update(synonyms)

    # 4. DM phonetic codes (с префиксом).
    for code in dm_soundex(name):
        out.add(f"{_DM_PREFIX}{code}")

    # Удалим пустые строки если случайно затесались (transliterate lib
    # иногда возвращает «» на edge-кейсах; гарантия минимум 1 элемента
    # в set'е сохраняется через входной ``name``).
    out.discard("")
    return out


__all__ = [
    "SourceLang",
    "generate_archive_variants",
]
