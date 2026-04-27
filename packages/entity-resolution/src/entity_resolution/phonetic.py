"""Phonetic-кодеры: Soundex (через pyphonetics) + Daitch-Mokotoff (own implementation).

Soundex — стандартный английский алгоритм, берём из ``pyphonetics``.

Daitch-Mokotoff Soundex (1985) — основной кодер для persons в AutoTreeGen
(см. ADR-0015 §«Daitch-Mokotoff»). Алгоритм публичный, описан в Avotaynu
Vol. I no. 3. Реализован здесь напрямую, потому что:

* ``pyphonetics`` 0.5.x не экспортирует Daitch-Mokotoff (только Soundex /
  Metaphone / RefinedSoundex / FuzzySoundex / Lein / MRA).
* Алгоритм короткий и не требует внешних зависимостей.
* Контроль над substitution table нам пригодится для кастомных правил
  (кириллица напрямую — Phase 3.4.x).

Алгоритм (упрощённая, но корректная для основных случаев версия):

1. Нормализация: uppercase, оставляем только A-Z.
2. Применяем substitution rules — длинные digrams/trigrams вперёд.
   Каждое правило даёт код в зависимости от позиции (start /
   before-vowel / other), плюс возможные ALTERNATE-варианты для
   неоднозначных диграмм (даёт несколько выходных кодов).
3. Удаляем подряд идущие одинаковые цифры.
4. Удаляем нули (vowel-codes за пределами начала).
5. Pad / truncate до 6 цифр.

Output — список из 1+ кодов. Два имени фонетически совпадают, если их
множества кодов пересекаются (см. ``persons.person_match_score``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pyphonetics import Soundex

if TYPE_CHECKING:
    from collections.abc import Iterable

_SOUNDEX = Soundex()


def soundex(name: str) -> str:
    """Стандартный Soundex код для имени.

    Возвращает строку из 4 символов (буква + 3 цифры). Пустая строка
    → ``""`` (pyphonetics на пустом вводе бросает исключение, мы это
    сглаживаем).
    """
    cleaned = name.strip()
    if not cleaned:
        return ""
    return str(_SOUNDEX.phonetics(cleaned))


# -------------------------------------------------------------------------
# Daitch-Mokotoff
# -------------------------------------------------------------------------
#
# Substitution table формат: каждая запись — (pattern, start_code,
# before_vowel_code, other_code). ``None`` означает «пропустить»
# (vowel в середине / конце слова → 0, который потом вырезается).
#
# Для ambiguous digrams (например, ``CH``) делаем 2 варианта в
# `_ALTERNATES` — выдаём несколько кодов на выходе.
#
# Это упрощённый набор, покрывающий ключевые случаи восточно-европейской
# еврейской генеалогии. Полная DM-таблица содержит ~60 правил; здесь —
# те, без которых Zhitnitzky/Zhytnicki / Schwartz / Mintz не свернутся
# к одинаковым кодам. Расширение — Phase 3.4.x по необходимости.

_VOWELS = frozenset("AEIOUY")

# (pattern, start_code, before_vowel_code, other_code).
# Длинные patterns должны идти раньше — ищем greedy.
_RULES: list[tuple[str, str, str, str]] = [
    # Triplets / doubles handled before single chars.
    ("SCH", "4", "4", "4"),
    ("ZSCH", "4", "4", "4"),
    ("ZSH", "4", "4", "4"),
    ("CSZ", "4", "4", "4"),
    ("CZS", "4", "4", "4"),
    ("DRZ", "4", "4", "4"),
    ("DRS", "4", "4", "4"),
    ("SHTSCH", "2", "4", "4"),
    ("SHTSH", "2", "4", "4"),
    # Двухбуквенные.
    ("CH", "5", "5", "5"),  # ALTERNATE: 4 (см. _ALTERNATES)
    ("CK", "5", "5", "5"),  # alternate: 45
    ("CS", "4", "4", "4"),
    ("CZ", "4", "4", "4"),
    ("DT", "3", "3", "3"),
    ("DS", "4", "4", "4"),
    ("DSH", "4", "4", "4"),
    ("DZ", "4", "4", "4"),
    ("KH", "5", "5", "5"),
    ("KS", "5", "54", "54"),
    ("PF", "7", "7", "7"),
    ("PH", "7", "7", "7"),
    ("RZ", "94", "94", "94"),  # alternate: 4
    ("RS", "94", "94", "94"),  # alternate: 4
    ("SCH", "4", "4", "4"),
    ("SH", "4", "4", "4"),
    ("ST", "2", "43", "43"),
    ("STSCH", "2", "4", "4"),
    ("STRZ", "2", "4", "4"),
    ("STSH", "2", "4", "4"),
    ("SZCZ", "2", "4", "4"),
    ("SZ", "4", "4", "4"),
    ("TC", "4", "4", "4"),
    ("TH", "3", "3", "3"),
    ("TRZ", "4", "4", "4"),
    ("TRS", "4", "4", "4"),
    ("TS", "4", "4", "4"),
    ("TSH", "4", "4", "4"),
    ("TSCH", "4", "4", "4"),
    ("TTS", "4", "4", "4"),
    ("TTSCH", "4", "4", "4"),
    ("TZ", "4", "4", "4"),
    ("TTZ", "4", "4", "4"),
    ("ZD", "2", "43", "43"),
    ("ZH", "4", "4", "4"),
    ("ZHD", "2", "43", "43"),
    ("ZHDZH", "2", "4", "4"),
    ("ZHTS", "4", "4", "4"),
    ("ZS", "4", "4", "4"),
    ("ZSCH", "4", "4", "4"),
    # Однобуквенные.
    ("A", "0", "", ""),
    ("E", "0", "", ""),
    ("I", "0", "", ""),
    ("O", "0", "", ""),
    ("U", "0", "", ""),
    ("Y", "1", "", ""),
    ("AI", "0", "1", ""),
    ("AJ", "0", "1", ""),
    ("AY", "0", "1", ""),
    ("AU", "0", "7", ""),
    ("EI", "0", "1", ""),
    ("EJ", "0", "1", ""),
    ("EY", "0", "1", ""),
    ("EU", "1", "1", ""),
    ("IA", "1", "", ""),
    ("IE", "1", "", ""),
    ("IO", "1", "", ""),
    ("IU", "1", "", ""),
    ("OI", "0", "1", ""),
    ("OJ", "0", "1", ""),
    ("OY", "0", "1", ""),
    ("UI", "0", "1", ""),
    ("UJ", "0", "1", ""),
    ("UY", "0", "1", ""),
    ("UE", "0", "", ""),
    ("J", "1", "", ""),  # alternate: 4
    ("B", "7", "7", "7"),
    ("C", "5", "5", "5"),  # alternate: 4
    ("D", "3", "3", "3"),
    ("F", "7", "7", "7"),
    ("G", "5", "5", "5"),
    ("H", "5", "5", ""),
    ("K", "5", "5", "5"),
    ("L", "8", "8", "8"),
    ("M", "6", "6", "6"),
    ("MN", "66", "66", "66"),
    ("N", "6", "6", "6"),
    ("NM", "66", "66", "66"),
    ("P", "7", "7", "7"),
    ("Q", "5", "5", "5"),
    ("R", "9", "9", "9"),
    ("S", "4", "4", "4"),
    ("T", "3", "3", "3"),
    ("V", "7", "7", "7"),
    ("W", "7", "7", "7"),
    ("X", "5", "54", "54"),
    ("Z", "4", "4", "4"),
]

# Сортируем правила по длине pattern (DESC), чтобы greedy-match брал
# самое длинное доступное. Делаем ОДИН раз при импорте модуля.
_RULES.sort(key=lambda r: -len(r[0]))

# ALTERNATE substitutions: на ключе — pattern, на значении — список
# дополнительных кодов (start, before_vowel, other), которые тоже
# учитываем, чтобы получить multiple output codes.
_ALTERNATES: dict[str, list[tuple[str, str, str]]] = {
    "C": [("4", "4", "4")],
    "CH": [("4", "4", "4")],
    "CK": [("45", "45", "45")],
    "J": [("4", "4", "4")],
    "RZ": [("4", "4", "4")],
    "RS": [("4", "4", "4")],
}

_NON_ALPHA = re.compile(r"[^A-Z]")


def _normalize(name: str) -> str:
    return _NON_ALPHA.sub("", name.upper())


def _is_vowel(ch: str) -> bool:
    return ch in _VOWELS


def _generate_codes(name: str) -> list[str]:
    """Развернуть DM коды для нормализованного имени.

    Возвращает 1+ строк по 6 цифр. Pure-функция, без побочек.
    """
    if not name:
        return []

    # Stack of partial codes — каждый элемент это (so_far, position_in_name).
    # Растим параллельно для всех ALTERNATE веток.
    branches: list[str] = [""]
    pos = 0

    while pos < len(name):
        # Найти самый длинный pattern, начинающийся с pos.
        matched: tuple[str, str, str, str] | None = None
        for rule in _RULES:
            pattern = rule[0]
            if name.startswith(pattern, pos):
                matched = rule
                break
        if matched is None:
            # Неизвестный символ — пропускаем (не должно случаться после _normalize).
            pos += 1
            continue

        pattern, start_code, before_vowel_code, other_code = matched
        # Position-conditional code:
        next_pos = pos + len(pattern)
        if pos == 0:
            code = start_code
        elif next_pos < len(name) and _is_vowel(name[next_pos]):
            code = before_vowel_code
        else:
            code = other_code

        # Apply alternate codes (если есть) — расширяем branches.
        primary_code = code
        alt_list = _ALTERNATES.get(pattern, [])
        alt_codes: list[str] = []
        for alt_start, alt_before_vowel, alt_other in alt_list:
            if pos == 0:
                alt_codes.append(alt_start)
            elif next_pos < len(name) and _is_vowel(name[next_pos]):
                alt_codes.append(alt_before_vowel)
            else:
                alt_codes.append(alt_other)

        new_branches: list[str] = []
        for branch in branches:
            new_branches.append(branch + primary_code)
            for alt in alt_codes:
                new_branches.append(branch + alt)
        branches = new_branches
        pos = next_pos

    # Post-process каждую ветку: убрать adjacent duplicates, нули
    # (кроме первого позиции), pad/truncate до 6 цифр.
    result: set[str] = set()
    for raw in branches:
        result.add(_finalize(raw))
    return sorted(result)


def _finalize(raw: str) -> str:
    """Schлифовать сырой код: dedupe adjacent, drop zeros (sauf-1st), pad/truncate to 6."""
    if not raw:
        return "000000"
    # Adjacent duplicates.
    deduped: list[str] = []
    last = ""
    for ch in raw:
        if ch != last:
            deduped.append(ch)
            last = ch
    # Drop zeros except possibly the first character.
    cleaned: list[str] = []
    for i, ch in enumerate(deduped):
        if ch == "0" and i > 0:
            continue
        cleaned.append(ch)
    code = "".join(cleaned)
    if len(code) >= 6:
        return code[:6]
    return code.ljust(6, "0")


def daitch_mokotoff(name: str) -> list[str]:
    """Daitch-Mokotoff коды (1+ строк по 6 цифр) для имени.

    Большинство имён дают 1 код, амбивалентные (Schwartz, Cohen, Mintz,
    Czarny) — 2+ кода. Считаем имена «фонетически совпадающими», если их
    множества кодов пересекаются (см. ``persons.person_match_score``).

    Пустая строка → ``[]``.
    """
    normalized = _normalize(name)
    if not normalized:
        return []
    return _generate_codes(normalized)


def _iter_distinct(codes: Iterable[str]) -> list[str]:
    """Уникализировать список кодов с сохранением порядка (helper для тестов)."""
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


__all__ = ["daitch_mokotoff", "soundex"]
