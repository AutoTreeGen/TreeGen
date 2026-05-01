"""Восточнославянский патронимический парсер (Phase 15.10 / ADR-0068).

Разбирает ``"Иван Иванович Петров"`` → :class:`ParsedName`(given, patronymic,
surname). Поддерживает четыре языка:

* ``ru`` — русский. Patronymic suffixes: ``-ович`` / ``-евич`` (м), ``-овна``
  / ``-евна`` (ж).
* ``uk`` — украинский. Тот же набор плюс ``-ійович`` / ``-їївна``.
* ``by`` — белорусский (тарашкевица + наркомовка). ``-авіч`` / ``-евіч`` (м),
  ``-аўна`` / ``-еўна`` (ж).
* ``pl`` — польский. Patronymics в современных именах редко используются;
  парсер для ``pl`` возвращает только ``given`` + ``surname`` (поле
  ``patronymic`` остаётся ``None``). Surname-эвристика — окончания
  ``-ski`` / ``-cki`` / ``-icz`` / женские ``-ska`` / ``-cka``.

Algorithm — context-free heuristic, не пытается резолвить ambiguity через
NLP / dictionaries:

1. ``len(tokens) == 1`` — либо given, либо surname. Распознаём surname
   через известные surname-окончания; иначе given.
2. ``len(tokens) == 2`` — given + surname (patronymic не выводится из 2-х
   токенов даже если средний выглядит как patronymic, потому что cum
   sole токеном всё равно не понятно где given/surname).
3. ``len(tokens) == 3`` — given + patronymic + surname если средний токен
   матчится на patronymic suffix; иначе given + 2 surname-tokens.
4. ``len(tokens) >= 4`` — first = given, last = surname, middle =
   patronymic если есть suffix-match; остальные складываются в surname
   («Анна Мария Ивановна Петрова-Сидорова» — двойная фамилия).

Edge cases (не raises'ит, лучше сохранить input в ``raw`` чем потерять):

* Пустая строка → ``ParsedName(given=None, patronymic=None, surname=None,
  raw="")``.
* Только whitespace → то же.
* Latin-форма патронимика («Ivan Ivanovich Petrov») — поддерживается через
  отдельный suffix-set с латинскими формами (``-ovich`` / ``-evich`` /
  ``-ovna`` / ``-evna``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Language = Literal["ru", "uk", "by", "pl"]


@dataclass(frozen=True, slots=True)
class ParsedName:
    """Структурированный разбор полного имени.

    Attributes:
        given: Имя (первый токен в типичных Cyrillic-словосочетаниях).
        patronymic: Отчество. ``None`` для языков без patronymic-института
            (pl) или когда heuristic не уверен.
        surname: Фамилия (последний токен в типичных Cyrillic-словосочетаниях,
            возможно склеенный с soft-дефис'ными частями).
        raw: Исходная строка, очищенная только от leading/trailing whitespace.
            Используется для round-trip'а при потере уверенности.
    """

    given: str | None
    patronymic: str | None
    surname: str | None
    raw: str


# Suffix-таблицы (lowercase). Длинные ставим вперёд: «евич» матчится до
# просто «ич» и т.п.
_PATRONYMIC_SUFFIXES_BY_LANG: Final[dict[Language, tuple[str, ...]]] = {
    "ru": (
        "ьевич",
        "ьевна",
        "евич",
        "евна",
        "ович",
        "овна",
        "ич",
        "инична",
    ),
    "uk": (
        "ійович",
        "їївна",
        "евич",
        "евна",
        "ович",
        "ївна",
        "івна",
    ),
    "by": (
        "евіч",
        "еўна",
        "авіч",
        "аўна",
        "евiч",  # narkamauka варианты с латинской «i»
        "авiч",
    ),
    "pl": (),  # современный pl — patronymics редкие, пропускаем
}

# Latin-romanized patronymic suffixes — общие для ru/uk/by-translit'ов.
_PATRONYMIC_SUFFIXES_LATIN: Final[tuple[str, ...]] = (
    "ovich",
    "evich",
    "ovna",
    "evna",
    "avich",
    "avna",
    "ewicz",  # Polish-style romanization для uk-фамилий иногда выглядит так
    "owicz",
)

# Surname-эндинги для disambiguation на single-token и Polish-парсинге.
# Lowercase. Cyrillic + Latin вперемешку — heuristic применяется к нижнему
# regex'у независимо от script'а.
_SURNAME_SUFFIXES: Final[tuple[str, ...]] = (
    # Cyrillic male
    "ов",
    "ев",
    "ин",
    "ын",
    "ский",
    "цкий",
    "ской",
    # Cyrillic female
    "ова",
    "ева",
    "ина",
    "ская",
    "цкая",
    # Polish male
    "ski",
    "cki",
    "icz",
    "owicz",
    "ewicz",
    # Polish female
    "ska",
    "cka",
    # Latin transliterations of Cyrillic patterns
    "ov",
    "ev",
    "off",  # historical Russian-emigré spelling
    "ev",
    "skiy",
    "skij",
    "sky",
)


def _has_patronymic_suffix(token: str, language: Language) -> bool:
    """Проверяем, оканчивается ли token на любой patronymic-suffix.

    Match'ится на lowercase'd версии и Cyrillic-, и Latin-окончаниях.
    Latin-suffix'ы общие для всех языков (paths через transliteration).
    """
    lowered = token.lower()
    cyrillic_suffixes = _PATRONYMIC_SUFFIXES_BY_LANG.get(language, ())
    return any(lowered.endswith(s) for s in cyrillic_suffixes) or any(
        lowered.endswith(s) for s in _PATRONYMIC_SUFFIXES_LATIN
    )


def _has_surname_suffix(token: str) -> bool:
    """Эвристическая проверка surname-окончания (не строгая, fail-soft)."""
    lowered = token.lower()
    return any(lowered.endswith(s) for s in _SURNAME_SUFFIXES)


class PatronymicParser:
    """Парсер русско / украинско / белорусско / польско-стиля имён.

    Args:
        language: Один из ``ru`` / ``uk`` / ``by`` / ``pl``. Влияет на
            набор Cyrillic patronymic-suffix'ов; Latin-suffix'ы общие.

    Тестовый паттерн::

        parser = PatronymicParser("ru")
        result = parser.parse("Иван Иванович Петров")
        assert result.given == "Иван"
        assert result.patronymic == "Иванович"
        assert result.surname == "Петров"
    """

    def __init__(self, language: Language) -> None:
        self._language: Final[Language] = language

    @property
    def language(self) -> Language:
        return self._language

    def parse(self, full_name: str) -> ParsedName:
        """Разобрать строку на (given, patronymic, surname).

        Никогда не raises'ит — для пустого ввода / unparseable строки
        возвращает ParsedName с ``raw`` и nullable полями.
        """
        cleaned = full_name.strip()
        if not cleaned:
            return ParsedName(given=None, patronymic=None, surname=None, raw="")

        tokens = [t for t in cleaned.split() if t]
        if not tokens:
            return ParsedName(given=None, patronymic=None, surname=None, raw=cleaned)

        if len(tokens) == 1:
            return self._parse_single(tokens[0], raw=cleaned)
        if len(tokens) == 2:
            return ParsedName(
                given=tokens[0],
                patronymic=None,
                surname=tokens[1],
                raw=cleaned,
            )
        if len(tokens) == 3:
            return self._parse_triple(tokens, raw=cleaned)
        return self._parse_long(tokens, raw=cleaned)

    # ----------------------------------------------------------- helpers

    def _parse_single(self, token: str, *, raw: str) -> ParsedName:
        """Single-token: surname или given, по surname-suffix-эвристике."""
        if _has_surname_suffix(token):
            return ParsedName(given=None, patronymic=None, surname=token, raw=raw)
        return ParsedName(given=token, patronymic=None, surname=None, raw=raw)

    def _parse_triple(self, tokens: list[str], *, raw: str) -> ParsedName:
        """Three tokens: given + (patronymic|extra surname) + surname."""
        first, middle, last = tokens
        if _has_patronymic_suffix(middle, self._language):
            return ParsedName(
                given=first,
                patronymic=middle,
                surname=last,
                raw=raw,
            )
        # Middle не похож на patronymic — двусоставная фамилия / ср. имя.
        return ParsedName(
            given=first,
            patronymic=None,
            surname=f"{middle} {last}",
            raw=raw,
        )

    def _parse_long(self, tokens: list[str], *, raw: str) -> ParsedName:
        """4+ tokens — first=given, last=surname; собираем middle.

        Если хоть один из middle-токенов — patronymic-suffix, выделяем
        первый такой как ``patronymic``; остаток сливаем в surname-prefix
        (двойная фамилия с дефисом / без). Если patronymic'а нет —
        всё middle уходит в surname как «doubled / multi-part» фамилия.
        """
        first = tokens[0]
        last = tokens[-1]
        middle = tokens[1:-1]

        patronymic_idx = next(
            (i for i, t in enumerate(middle) if _has_patronymic_suffix(t, self._language)),
            None,
        )
        if patronymic_idx is None:
            # Нет patronymic — middle присоединяется к surname как
            # part'ы compound-фамилии (часто разделены пробелом).
            joined_middle = " ".join(middle)
            surname = f"{joined_middle} {last}".strip()
            return ParsedName(
                given=first,
                patronymic=None,
                surname=surname,
                raw=raw,
            )
        patronymic = middle[patronymic_idx]
        rest = middle[:patronymic_idx] + middle[patronymic_idx + 1 :]
        surname = " ".join(rest) + " " + last if rest else last
        return ParsedName(
            given=first,
            patronymic=patronymic,
            surname=surname.strip(),
            raw=raw,
        )


__all__ = ["Language", "ParsedName", "PatronymicParser"]
