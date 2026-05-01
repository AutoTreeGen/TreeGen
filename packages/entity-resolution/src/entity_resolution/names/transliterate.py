"""Multi-script transliteration (Phase 15.10 / ADR-0068).

Cyrillic ↔ Latin (multiple romanization standards), Hebrew ↔ Latin (BGN /
LOC), Yiddish↔Hebrew custom rules + Polish/German/Czech diacritic fold +
restore-candidates.

Архитектура: один :class:`Transliterator` с методами на каждое направление.
Никаких runtime side-effects — все mapping-таблицы — module-level
constants. Public контракт см. в docstring'ах методов.

Внешние зависимости:

* ``unidecode`` — для diacritic fold'а (Polish/German/Czech). Cyrillic-
  через-unidecode даёт «generic Latin» без BGN/ISO-9/LOC distinct'а,
  поэтому Cyrillic-маппинг — собственные таблицы.
* ``transliterate`` (PyPI) — для **Latin → Cyrillic** GOST-7.79-default
  best-guess (ru-routing). Latin → Hebrew, Hebrew → Latin — собственные
  таблицы (transliterate lib не покрывает иврит).

Ограничения V1 (Phase 15.10):

* Yiddish detection — не реализовано (требует lexicon'а Yiddish forms);
  ``to_hebrew(source_script='yiddish')`` использует тот же inverse-map,
  что и ``source_script='latin'`` — caller документирует на app-уровне,
  что Yiddish input должен предварительно canonicalize'аться. Phase 10.9.x
  расширит.
* Soft sign / hard sign / ё-recovery in Latin→Cyrillic: best-effort через
  ``transliterate`` lib; точность зависит от входного текста.
* Multi-output (homophone candidates) — out of scope V1; каждый
  ``to_*`` возвращает один deterministic вариант.
"""

from __future__ import annotations

from typing import Final, Literal

from transliterate import translit
from unidecode import unidecode

LatinStandard = Literal["bgn", "iso9", "loc"]
HebrewStandard = Literal["bgn", "loc"]
SourceScriptToLatin = Literal["cyrillic", "hebrew", "yiddish"]
SourceScriptToCyrillic = Literal["latin", "hebrew"]
SourceScriptToHebrew = Literal["latin", "yiddish"]
DiacriticLang = Literal["pl", "de", "cs"]


# --------------------------------------------------------------------------
# Cyrillic → Latin maps
# --------------------------------------------------------------------------

# BGN/PCGN romanization (English-speaking archives default).
_CYRILLIC_TO_LATIN_BGN: Final[dict[str, str]] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": '"',
    "ы": "y",
    "ь": "'",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    # Ukrainian / Belarusian extensions.
    "є": "ye",
    "і": "i",
    "ї": "yi",
    "ґ": "g",
    "ў": "w",
}

# ISO 9 (academic, 1:1 reversible).
_CYRILLIC_TO_LATIN_ISO9: Final[dict[str, str]] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "ë",
    "ж": "ž",
    "з": "z",
    "и": "i",
    "й": "j",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "č",
    "ш": "š",
    "щ": "ŝ",
    "ъ": "ʺ",
    "ы": "y",
    "ь": "ʹ",
    "э": "è",
    "ю": "û",
    "я": "â",
    "є": "ê",
    "і": "ì",
    "ї": "ï",
    "ґ": "g̀",
    "ў": "ŭ",
}

# Library of Congress (American library catalogues / older archives).
_CYRILLIC_TO_LATIN_LOC: Final[dict[str, str]] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "ë",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "ĭ",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "ʺ",
    "ы": "y",
    "ь": "ʹ",
    "э": "ė",
    "ю": "iu",
    "я": "ia",
    "є": "ie",
    "і": "i",
    "ї": "ï",
    "ґ": "g",
    "ў": "ŭ",
}

_CYRILLIC_LATIN_MAPS: Final[dict[LatinStandard, dict[str, str]]] = {
    "bgn": _CYRILLIC_TO_LATIN_BGN,
    "iso9": _CYRILLIC_TO_LATIN_ISO9,
    "loc": _CYRILLIC_TO_LATIN_LOC,
}


# --------------------------------------------------------------------------
# Hebrew → Latin maps
# --------------------------------------------------------------------------

# Hebrew letters (consonant root). Vowel pointing (niqqud) выпадает —
# фамилии в архивах обычно без niqqud. Final-letter forms (ך/ם/ן/ף/ץ)
# нормализуются к их обычной форме.
_HEBREW_FINAL_NORMALIZE: Final[dict[str, str]] = {
    "ך": "כ",
    "ם": "מ",
    "ן": "נ",
    "ף": "פ",
    "ץ": "צ",
}

_HEBREW_TO_LATIN_BGN: Final[dict[str, str]] = {
    "א": "'",
    "ב": "v",
    "ג": "g",
    "ד": "d",
    "ה": "h",
    "ו": "v",
    "ז": "z",
    "ח": "kh",
    "ט": "t",
    "י": "y",
    "כ": "kh",
    "ל": "l",
    "מ": "m",
    "נ": "n",
    "ס": "s",
    "ע": "'",
    "פ": "p",
    "צ": "ts",
    "ק": "k",
    "ר": "r",
    "ש": "sh",
    "ת": "t",
}

_HEBREW_TO_LATIN_LOC: Final[dict[str, str]] = {
    "א": "ʼ",
    "ב": "b",
    "ג": "g",
    "ד": "d",
    "ה": "h",
    "ו": "w",
    "ז": "z",
    "ח": "ḥ",
    "ט": "ṭ",
    "י": "y",
    "כ": "k",
    "ל": "l",
    "מ": "m",
    "נ": "n",
    "ס": "s",
    "ע": "ʻ",
    "פ": "p",
    "צ": "ṣ",
    "ק": "q",
    "ר": "r",
    "ש": "sh",
    "ת": "t",
}

_HEBREW_LATIN_MAPS: Final[dict[HebrewStandard, dict[str, str]]] = {
    "bgn": _HEBREW_TO_LATIN_BGN,
    "loc": _HEBREW_TO_LATIN_LOC,
}


# --------------------------------------------------------------------------
# Latin → Hebrew (best-guess, для variants generation)
# --------------------------------------------------------------------------

# Inverse mapping для best-guess Hebrew form'а из Latin'а. Multi-char
# patterns ставим вперёд: «sh»/«ch»/«ts»/«kh» матчатся раньше single
# letters. Намеренно lossy (Hebrew 22-letter alphabet ↔ 26 Latin letters
# даёт ambiguity) — это «лучшее предположение» для ICP-search'а, не
# восстановление оригинала.
_LATIN_TO_HEBREW_RULES: Final[list[tuple[str, str]]] = [
    ("sh", "ש"),
    ("ch", "כ"),
    ("kh", "ח"),
    ("ts", "צ"),
    ("zh", "ז"),
    ("a", "א"),
    ("b", "ב"),
    ("v", "ב"),
    ("g", "ג"),
    ("d", "ד"),
    ("h", "ה"),
    ("e", "ע"),
    ("w", "ו"),
    ("u", "ו"),
    ("o", "ו"),
    ("z", "ז"),
    ("t", "ת"),
    ("i", "י"),
    ("y", "י"),
    ("j", "י"),
    ("k", "כ"),
    ("l", "ל"),
    ("m", "מ"),
    ("n", "נ"),
    ("s", "ס"),
    ("p", "פ"),
    ("f", "פ"),
    ("c", "ק"),
    ("q", "ק"),
    ("r", "ר"),
    ("x", "כס"),
]


# --------------------------------------------------------------------------
# Diacritic restore-candidate maps (per language)
# --------------------------------------------------------------------------

# Польский: ASCII-форма + native — оба варианта попадают в archive search.
# Map fold-result → list-of-restore-candidates: «l» → ["l", "ł"] и т.п.
_POLISH_RESTORE_RULES: Final[list[tuple[str, str]]] = [
    ("l", "ł"),
    ("L", "Ł"),
    ("a", "ą"),
    ("A", "Ą"),
    ("e", "ę"),
    ("E", "Ę"),
    ("c", "ć"),
    ("C", "Ć"),
    ("n", "ń"),
    ("N", "Ń"),
    ("o", "ó"),
    ("O", "Ó"),
    ("s", "ś"),
    ("S", "Ś"),
    ("z", "ż"),
    ("Z", "Ż"),
]

# Немецкий: ä↔ae round-trip (Müller ↔ Mueller — оба валидных).
_GERMAN_FOLD_RULES: Final[list[tuple[str, str]]] = [
    ("ä", "ae"),
    ("Ä", "Ae"),
    ("ö", "oe"),
    ("Ö", "Oe"),
    ("ü", "ue"),
    ("Ü", "Ue"),
    ("ß", "ss"),
]

# Чешский: hacek-формы.
_CZECH_FOLD_RULES: Final[list[tuple[str, str]]] = [
    ("č", "c"),
    ("Č", "C"),
    ("š", "s"),
    ("Š", "S"),
    ("ž", "z"),
    ("Ž", "Z"),
    ("ř", "r"),
    ("Ř", "R"),
    ("ě", "e"),
    ("Ě", "E"),
    ("ý", "y"),
    ("Ý", "Y"),
]


def _apply_table(text: str, table: dict[str, str]) -> str:
    """Char-by-char apply mapping table; unknown chars passthrough.

    Casing rule: для uppercase input-char'а делаем title-case mapped'а
    (``Ж`` → ``Zh``, не ``ZH``). Это match'ит обычное name-write convention'а
    (Capitalised given/surname, нижний регистр для остальных букв).
    """
    out: list[str] = []
    for ch in text:
        if ch.lower() in table:
            mapped = table[ch.lower()]
            if ch.isupper() and mapped:
                mapped = mapped[0].upper() + mapped[1:]
            out.append(mapped)
        else:
            out.append(ch)
    return "".join(out)


def _normalize_hebrew_finals(text: str) -> str:
    """Normalize final-letter forms (ך/ם/ן/ף/ץ) к обычным perform map'у."""
    return "".join(_HEBREW_FINAL_NORMALIZE.get(ch, ch) for ch in text)


def _apply_replacements(text: str, rules: list[tuple[str, str]]) -> str:
    """Apply ordered list of (find, replace) — длинные паттерны идут вперёд."""
    for find, replace in rules:
        text = text.replace(find, replace)
    return text


class Transliterator:
    """Multi-script transliterator. Stateless — методы pure functions.

    Тестовый паттерн::

        tl = Transliterator()
        assert tl.to_latin("Левитин", source_script="cyrillic", standard="bgn") == "Levitin"
        assert tl.to_latin("Левитин", source_script="cyrillic", standard="loc") == "Levitin"
        # Diacritic fold + restore:
        candidates = tl.normalize_diacritics("Müller", lang="de")
        assert "Mueller" in candidates
        assert "Müller" in candidates
    """

    # ----------------------------------------------------------- to_latin

    def to_latin(
        self,
        text: str,
        *,
        source_script: SourceScriptToLatin,
        standard: LatinStandard = "bgn",
    ) -> str:
        """Транслитерация в Latin script.

        Args:
            text: Исходная строка.
            source_script: ``cyrillic`` / ``hebrew`` / ``yiddish``. Для
                ``yiddish`` используется Hebrew-таблица (Yiddish lexicon —
                follow-up).
            standard: ``bgn`` (default, English archives) / ``iso9`` (academic) /
                ``loc`` (LoC). Для Hebrew поддерживаются ``bgn`` и ``loc``;
                ``iso9`` для иврита fall-back'ом на ``loc``.

        Returns:
            Latin-форма. Unknown chars (digits / punctuation) — passthrough.
        """
        if not text:
            return ""
        if source_script == "cyrillic":
            table = _CYRILLIC_LATIN_MAPS[standard]
            return _apply_table(text, table)
        if source_script in ("hebrew", "yiddish"):
            normalized = _normalize_hebrew_finals(text)
            heb_std: HebrewStandard = "loc" if standard == "iso9" else standard
            table = _HEBREW_LATIN_MAPS[heb_std]
            return _apply_table(normalized, table)
        return text  # pragma: no cover — Literal exhaustiveness

    # -------------------------------------------------------- to_cyrillic

    def to_cyrillic(
        self,
        text: str,
        *,
        source_script: SourceScriptToCyrillic,
    ) -> str:
        """Транслитерация в Cyrillic script (best-guess).

        Используется ``transliterate`` lib (russian) для Latin → Cyrillic;
        Hebrew → Cyrillic — через Hebrew → Latin → Cyrillic chain (lossy,
        но всё равно даёт useful match для archive search).
        """
        if not text:
            return ""
        if source_script == "latin":
            try:
                # transliterate lib возвращает ``Any`` (нет stubs); каст
                # на ``str`` чтобы mypy strict не шумел no-any-return.
                return str(translit(text, "ru"))
            except Exception:
                # transliterate lib raises на нестандартных входах
                # (e.g. чисто-латинская диакритика) — fail-soft.
                return text
        if source_script == "hebrew":
            latin = self.to_latin(text, source_script="hebrew", standard="bgn")
            return self.to_cyrillic(latin, source_script="latin")
        return text  # pragma: no cover

    # ---------------------------------------------------------- to_hebrew

    def to_hebrew(
        self,
        text: str,
        *,
        source_script: SourceScriptToHebrew,
    ) -> str:
        """Транслитерация в Hebrew script (best-guess для archive search).

        Lossy на 26→22 letter mapping; Yiddish-форма пока fall-back'ом на
        Latin-rules (lexicon — Phase 10.9.x).
        """
        # source_script reserved для будущего routing'а на Yiddish lexicon —
        # подавляем ARG002 явно через unused-bind, чтобы API V1 уже включал
        # параметр (callers не ломаются при будущем split'е).
        _ = source_script
        if not text:
            return ""
        # Latin / Yiddish (V1: оба через Latin-rules; Yiddish lexicon TODO).
        return _apply_replacements(text.lower(), _LATIN_TO_HEBREW_RULES)

    # -------------------------------------------- normalize_diacritics

    def normalize_diacritics(self, text: str, *, lang: DiacriticLang) -> set[str]:
        """Diacritic fold + restore-candidates для archive variants.

        Args:
            text: Исходная строка. Должна быть Latin-script — Cyrillic /
                Hebrew input возвращается без изменений (cross-script
                fold — это задача :meth:`to_latin`, не diacritic'а).
            lang: ``pl`` / ``de`` / ``cs`` — какие native-restore rule'ы
                применить.

        Returns:
            ``set`` включающий original input + language-specific
            ASCII-fold и restore-candidates. Минимум 1 элемент (input
            всегда внутри). Покрывает ``Müller`` / ``Mueller``,
            ``Łukasz`` / ``Lukasz``, ``Černý`` / ``Cerny``.

        Important:
            **Не** используем :func:`unidecode.unidecode` универсально —
            он фолдит и Cyrillic, и Hebrew, что испортило бы
            cross-script reason-attribution (``Levitin`` ≡ ``Левитин``
            попало бы в ``variant_diacritic`` вместо
            ``variant_transliteration``).
        """
        out: set[str] = {text}
        if not text:
            return out
        if lang == "pl":
            # ASCII-fold для Polish via unidecode (только если input —
            # Latin-script; иначе skip, см. модульную ноту).
            if _is_latin_script(text):
                out.add(unidecode(text))
            for ascii_ch, native_ch in _POLISH_RESTORE_RULES:
                if ascii_ch in text and native_ch not in text:
                    out.add(text.replace(ascii_ch, native_ch))
        elif lang == "de":
            # German ä↔ae round-trip: и folded → ae-form, и native-form.
            out.add(_apply_replacements(text, _GERMAN_FOLD_RULES))
            reverse_rules = [(b, a) for a, b in _GERMAN_FOLD_RULES]
            out.add(_apply_replacements(text, reverse_rules))
        elif lang == "cs":
            out.add(_apply_replacements(text, _CZECH_FOLD_RULES))
        return out


def _is_latin_script(text: str) -> bool:
    """True если хотя бы половина буквенных символов — Latin-block.

    Используется в :meth:`Transliterator.normalize_diacritics` чтобы НЕ
    применять ``unidecode`` к Cyrillic / Hebrew input'у (см. там же).
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    latin = sum(1 for ch in letters if "A" <= ch.upper() <= "Z" or ch in "ÄäÖöÜüß")
    return latin >= len(letters) / 2


__all__ = [
    "DiacriticLang",
    "HebrewStandard",
    "LatinStandard",
    "SourceScriptToCyrillic",
    "SourceScriptToHebrew",
    "SourceScriptToLatin",
    "Transliterator",
]
