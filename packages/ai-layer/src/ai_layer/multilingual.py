"""Phase 10.9e — multilingual helpers for voice-to-tree extraction.

Slice A (this PR) ships only the **transliteration helper**: takes a
person-name string + ISO-639 locale hint and returns ``{"original",
"latin"}`` using the Phase 15.10 :class:`Transliterator`. Slice B (after
10.9b lands) will wire the helper into the actual extraction pipeline +
persist the dual form on ``voice_extracted_proposal.transliterated_names``.

Design notes (see ADR-0080):

* No new transliteration logic — full delegation to 15.10. This module is
  ~80 LOC of glue: locale → script mapping, auto-detect for ``locale=None``,
  and the ``{original, latin}`` shape downstream consumers expect.
* English / already-Latin input is a passthrough: ``{"original": name,
  "latin": name}``. Identity matters: regression-stable for Geoffrey-demo
  EN flow.
* Auto-detect (when ``locale`` is None or ``"auto"``) inspects the input
  string for Cyrillic / Hebrew Unicode block membership. We do not call
  external language-detection libraries — Whisper handles transcription
  language detection upstream; here we only need script detection, which
  is deterministic from the bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

from entity_resolution.names.transliterate import Transliterator

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransliteratedName:
    """Pair-form вывод :func:`transliterate_for_locale`.

    Поля:
        original: Имя как пришло (preserve verbatim, no normalization).
        latin: Latin-форма (BGN/PCGN для Cyrillic, ALA-LC для Hebrew).
            Равно ``original`` для уже-Latin input'ов.
        script: Detected script bucket (``"latin"`` / ``"cyrillic"`` /
            ``"hebrew"``). Полезно downstream callers для UI hint'ов.
    """

    original: str
    latin: str
    script: str


# Whisper-compatible ISO-639 коды → script-bucket из 15.10.
_LOCALE_TO_SCRIPT: dict[str, str] = {
    "en": "latin",
    "ru": "cyrillic",
    "uk": "cyrillic",
    "be": "cyrillic",
    "bg": "cyrillic",
    "sr": "cyrillic",
    "he": "hebrew",
    "yi": "hebrew",  # Yiddish reuses Hebrew script bucket per 15.10
}


# Unicode-block heuristics для auto-detect (когда locale None / "auto").
# Берём первый non-whitespace, non-punctuation символ — Names short, mixing
# scripts in одном слове редкость; этого достаточно V1.
def _detect_script_from_text(text: str) -> str:
    """Heuristic: первый script-bearing символ → bucket.

    Возвращает ``"latin"`` если ни Cyrillic ни Hebrew не найдены — fail-safe
    для names типа "John 王" где первый script-символ не из 3 наших buckets.
    """
    for ch in text:
        codepoint = ord(ch)
        # Cyrillic Unicode blocks: основной 0x0400-0x04FF + Cyrillic
        # Supplement 0x0500-0x052F + Extended-A 0x2DE0-0x2DFF.
        if 0x0400 <= codepoint <= 0x052F or 0x2DE0 <= codepoint <= 0x2DFF:
            return "cyrillic"
        # Hebrew Unicode block 0x0590-0x05FF (covers Hebrew + Yiddish letters).
        if 0x0590 <= codepoint <= 0x05FF:
            return "hebrew"
    return "latin"


def transliterate_for_locale(
    name: str,
    locale: str | None = None,
    *,
    transliterator: Transliterator | None = None,
) -> TransliteratedName:
    """Перевести имя в Latin с сохранением оригинала.

    Args:
        name: Person-name строка (без CR/LF — preserve verbatim как пришла
            из транскрипции).
        locale: ISO-639 hint от caller (e.g. user-selected language picker
            value из Phase 10.9d frontend). ``None`` / ``""`` / ``"auto"``
            → auto-detect по script-блоку Unicode'а первого symbol'а.
        transliterator: DI hook для тестов; обычно None (создаём fresh
            stateless instance).

    Returns:
        :class:`TransliteratedName` с полями ``original``, ``latin``,
        ``script``. Empty input → empty оба + ``script="latin"``.

    Examples:
        >>> r = transliterate_for_locale("Иван Петрович", locale="ru")
        >>> r.original
        'Иван Петрович'
        >>> r.latin
        'Ivan Petrovich'
        >>> r.script
        'cyrillic'

        >>> r = transliterate_for_locale("John Doe")
        >>> r.original == r.latin == "John Doe"
        True
    """
    if not name:
        return TransliteratedName(original="", latin="", script="latin")

    script = _resolve_script(name, locale)
    if script == "latin":
        return TransliteratedName(original=name, latin=name, script="latin")

    tl = transliterator or Transliterator()
    if script == "cyrillic":
        latin = tl.to_latin(name, source_script="cyrillic", standard="bgn")
    else:  # hebrew (or yiddish — bucket 15.10 collapses)
        latin = tl.to_latin(name, source_script="hebrew", standard="loc")

    return TransliteratedName(original=name, latin=latin, script=script)


def _resolve_script(name: str, locale: str | None) -> str:
    """Locale-explicit > auto-detect from text > 'latin' fallback."""
    if locale and locale != "auto":
        mapped = _LOCALE_TO_SCRIPT.get(locale)
        if mapped is not None:
            return mapped
    return _detect_script_from_text(name)


__all__ = [
    "TransliteratedName",
    "transliterate_for_locale",
]
