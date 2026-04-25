"""Нормализация дат GEDCOM 5.5.5 §3.6.

Грамматика, которую разбираем:

* Точная дата: ``YEAR``, ``MONTH YEAR``, ``DAY MONTH YEAR``.
* Двойной год: ``1750/51`` (Old/New Style transition в Англии XVIII в.).
* Эра: суффикс ``BC`` / ``B.C.``.
* Quantifier'ы: ``ABT`` / ``CAL`` / ``EST`` (приблизительно), ``BEF`` / ``AFT``
  (открытая граница), ``INT`` (интерпретированное).
* Period: ``FROM x TO y``, ``FROM x``, ``TO y``.
* Range: ``BET x AND y``.
* Phrase: ``(текст)`` отдельно или после ``INT date``.
* Calendar escapes: ``@#DGREGORIAN@``, ``@#DJULIAN@``, ``@#DHEBREW@``,
  ``@#DFRENCH R@``, ``@#DROMAN@``, ``@#DUNKNOWN@``.

Поддерживаются: Gregorian, Julian (proleptic, по алгоритму Meeus),
Hebrew (религиозная нумерация месяцев, гражданский год — через
``convertdate.hebrew``), French Republican (через
``convertdate.french_republican``). Roman / Unknown компоненты
распознаются, но bracketing — ``None`` (нет общепринятой конверсии).

Вход всегда сохраняется в ``ParsedDate.raw`` для round-trip без потерь.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from convertdate import french_republican as _french_republican  # type: ignore[import-untyped]
from convertdate import hebrew as _hebrew
from pydantic import BaseModel, ConfigDict, Field

from gedcom_parser.exceptions import GedcomDateParseError

# -----------------------------------------------------------------------------
# Типы и константы
# -----------------------------------------------------------------------------

Calendar = Literal["gregorian", "julian", "hebrew", "french-r", "roman", "unknown"]
"""Распознаваемые календари. Преобразование в Gregorian — только для julian."""

Qualifier = Literal["none", "ABT", "CAL", "EST", "BEF", "AFT", "INT"]
"""Маркер неопределённости даты.

``ABT``/``CAL``/``EST`` — три варианта приблизительности (estimate, calculated,
about). ``BEF``/``AFT`` — открытая граница. ``INT`` — интерпретированная
вручную дата с пояснением в ``phrase``. ``none`` — точная или диапазон/период.
"""


_GREGORIAN_MONTHS: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# Hebrew месяцы — GEDCOM 3-буквенные коды → индексы convertdate.hebrew
# (1=Nisan религиозного порядка). Год в convertdate использует ГРАЖДАНСКОЕ
# обновление: год X начинается на Tishri 1, X (это месяц 7 в религиозной
# нумерации). Поэтому все месяцы X — от Tishri (m=7) до Elul (m=6) — все
# относятся к одному году X. Эта же логика работает для bracketing'а года.
_HEBREW_MONTH_MAP: dict[str, int] = {
    "TSH": 7,  # Tishri (начало гражданского года)
    "CSH": 8,  # Cheshvan / Marcheshvan / Heshvan
    "KSL": 9,  # Kislev
    "TVT": 10,  # Tevet / Teveth
    "SHV": 11,  # Shevat
    "ADR": 12,  # Adar (или Adar I в високосный год)
    "ADS": 13,  # Adar Sheni / Adar II / Adar Bet — только в високосный год
    "NSN": 1,  # Nisan (начало религиозного года)
    "IYR": 2,  # Iyyar
    "SVN": 3,  # Sivan
    "TMZ": 4,  # Tammuz
    "AAV": 5,  # Av
    "ELL": 6,  # Elul (конец гражданского года)
}

# French Republican месяцы — GEDCOM 4-буквенные коды → индексы 1..13.
# Месяцы 1..12 имеют ровно 30 дней. Месяц 13 (Sansculottides) — 5 дней
# в обычный год, 6 дней в високосный (см. french_republican.leap()).
_FRENCH_MONTH_MAP: dict[str, int] = {
    "VEND": 1,  # Vendémiaire
    "BRUM": 2,  # Brumaire
    "FRIM": 3,  # Frimaire
    "NIVO": 4,  # Nivôse
    "PLUV": 5,  # Pluviôse
    "VENT": 6,  # Ventôse
    "GERM": 7,  # Germinal
    "FLOR": 8,  # Floréal
    "PRAI": 9,  # Prairial
    "MESS": 10,  # Messidor
    "THER": 11,  # Thermidor
    "FRUC": 12,  # Fructidor
    "COMP": 13,  # Sansculottides (jours complémentaires)
}


_CALENDAR_ESCAPE_MAP: dict[str, Calendar] = {
    "@#DGREGORIAN@": "gregorian",
    "@#DJULIAN@": "julian",
    "@#DHEBREW@": "hebrew",
    "@#DFRENCH R@": "french-r",
    "@#DROMAN@": "roman",
    "@#DUNKNOWN@": "unknown",
}

# Календари с поддержкой проекции в proleptic Gregorian для bracketing'а.
# В этой итерации добавлены Hebrew и French Republican через библиотеку
# convertdate (ROADMAP §5.1.5b). Roman / Unknown по-прежнему без bracketing'а.
_CALENDARS_WITH_BRACKETING: frozenset[Calendar] = frozenset(
    {"gregorian", "julian", "hebrew", "french-r"}
)

_DAYS_IN_MONTH_NON_LEAP: tuple[int, ...] = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


# -----------------------------------------------------------------------------
# Модель
# -----------------------------------------------------------------------------


class ParsedDate(BaseModel):
    """Нормализованная дата GEDCOM.

    Поля делятся на три группы:

    1. ``raw`` — оригинальная строка (для round-trip без потерь).
    2. ``calendar`` / ``qualifier`` / ``is_period`` / ``is_range`` / ``phrase``
       — структурный разбор: какой календарь, какой тип неопределённости,
       свободный текст из ``INT`` или скобок.
    3. ``date_lower`` / ``date_upper`` — bracketing в proleptic Gregorian.
       Полузакрытый диапазон возможен (``BEF`` → только ``date_upper``,
       ``AFT`` → только ``date_lower``). Для нон-(Gregorian/Julian) —
       обе границы ``None`` (см. модульный docstring).

    Семантика bracketing'а для Gregorian/Julian:

    * Год ``1850``                       → ``[1850-01-01, 1850-12-31]``
    * Месяц ``JAN 1850``                 → ``[1850-01-01, 1850-01-31]``
    * Точная дата ``1 JAN 1850``         → ``[1850-01-01, 1850-01-01]``
    * ``BEF 1850``                       → ``[None, 1849-12-31]`` (строго раньше)
    * ``AFT 1850``                       → ``[1851-01-01, None]`` (строго позже)
    * ``BEF 5 JUN 1850``                 → ``[None, 1850-06-04]``
    * ``AFT 5 JUN 1850``                 → ``[1850-06-06, None]``
    * ``BET 1840 AND 1850``              → ``[1840-01-01, 1850-12-31]``
    * ``FROM 1840 TO 1850``              → ``[1840-01-01, 1850-12-31]`` (плюс
      ``is_period=True``: «непрерывно», в отличие от range «когда-то между»)

    Дата до 1 года н.э. (``BC``) и любая дата за пределами ``datetime.date``
    (год ≤ 0 или > 9999) → bracketing ``None`` при сохранении остальных полей.
    """

    raw: str = Field(description="Оригинальная строка тега DATE.")
    calendar: Calendar = "gregorian"
    qualifier: Qualifier = "none"
    is_period: bool = Field(
        default=False,
        description="True для FROM..TO / FROM / TO (непрерывный интервал).",
    )
    is_range: bool = Field(
        default=False,
        description="True для BET..AND (одна точка, неизвестно где в интервале).",
    )
    date_lower: date | None = Field(
        default=None,
        description="Нижняя граница в proleptic Gregorian (None — открыта).",
    )
    date_upper: date | None = Field(
        default=None,
        description="Верхняя граница в proleptic Gregorian (None — открыта).",
    )
    phrase: str | None = Field(
        default=None,
        description="Текст из INT date (phrase) или из чистой формы (phrase).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# -----------------------------------------------------------------------------
# Julian → proleptic Gregorian (алгоритм Meeus / Fliegel-Van Flandern)
# -----------------------------------------------------------------------------


def _julian_date_to_jdn(year: int, month: int, day: int) -> int:
    """Julian-календарь → Julian Day Number (целочисленно)."""
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083


def _jdn_to_gregorian(jdn: int) -> tuple[int, int, int]:
    """Julian Day Number → дата по proleptic Gregorian как ``(год, месяц, день)``."""
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    g_day = e - (153 * m + 2) // 5 + 1
    g_month = m + 3 - 12 * (m // 10)
    g_year = 100 * b + d - 4800 + (m // 10)
    return g_year, g_month, g_day


def julian_to_gregorian(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Перевести дату из юлианского календаря в proleptic Gregorian.

    Args:
        year: Год по юлианскому календарю (>= 1; для BC возвращается ошибка).
        month: 1..12.
        day: 1..(дни в месяце по юлианскому).

    Returns:
        Кортеж ``(год, месяц, день)`` в proleptic Gregorian.

    Raises:
        ValueError: При некорректных компонентах.
    """
    if year < 1:
        msg = f"Julian year must be >= 1 (got {year})"
        raise ValueError(msg)
    if not 1 <= month <= 12:
        msg = f"Julian month must be 1..12 (got {month})"
        raise ValueError(msg)
    max_day = _days_in_julian_month(year, month)
    if not 1 <= day <= max_day:
        msg = f"Julian day must be 1..{max_day} for {year}-{month:02d} (got {day})"
        raise ValueError(msg)
    jdn = _julian_date_to_jdn(year, month, day)
    return _jdn_to_gregorian(jdn)


def hebrew_to_gregorian(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Перевести Hebrew-дату (религиозная нумерация месяцев) в proleptic Gregorian.

    Месяцы — религиозный порядок, как в convertdate: 1=Nisan, 7=Tishri,
    13=Adar Bet (только в високосный год). Год — гражданская нумерация:
    год X начинается на Tishri 1 и заканчивается на Elul 29/30. Это значит,
    что Nisan 5780 (m=1) попадает на весну Greg-2020, а Tishri 5780 (m=7) —
    на осень Greg-2019. Обе даты внутри Hebrew-года 5780.

    Raises:
        ValueError: При некорректных компонентах.
    """
    return _hebrew.to_gregorian(year, month, day)  # type: ignore[no-any-return]


def french_republican_to_gregorian(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Перевести дату Французского Республиканского календаря в proleptic Gregorian.

    Месяцы 1..12 имеют по 30 дней; месяц 13 (Sansculottides / jours
    complémentaires) — 5 дней в обычный год, 6 в високосный (см.
    :func:`convertdate.french_republican.leap`). Год 1 начался 22 сентября
    1792 г. Календарь использовался до 1805 г.

    Raises:
        ValueError: При некорректных компонентах.
    """
    return _french_republican.to_gregorian(year, month, day)  # type: ignore[no-any-return]


def _is_julian_leap(year: int) -> bool:
    """Юлианский год — високосный, если делится на 4 (без правил Gregorian)."""
    return year % 4 == 0


def _is_gregorian_leap(year: int) -> bool:
    """Григорианский високосный: %4 и (не %100, или %400)."""
    return (year % 4 == 0) and (year % 100 != 0 or year % 400 == 0)


def _days_in_julian_month(year: int, month: int) -> int:
    base = _DAYS_IN_MONTH_NON_LEAP[month - 1]
    if month == 2 and _is_julian_leap(year):
        return 29
    return base


def _days_in_gregorian_month(year: int, month: int) -> int:
    base = _DAYS_IN_MONTH_NON_LEAP[month - 1]
    if month == 2 and _is_gregorian_leap(year):
        return 29
    return base


# -----------------------------------------------------------------------------
# Парсинг компонентов
# -----------------------------------------------------------------------------

_DUAL_YEAR_RE: re.Pattern[str] = re.compile(r"^(\d{1,4})/(\d{1,2})$")
_PLAIN_YEAR_RE: re.Pattern[str] = re.compile(r"^\d{1,4}$")


def _parse_year_token(token: str) -> int:
    """Распарсить год из токена.

    Поддерживает:

    * Простой год: ``1850``, ``850``, ``50``.
    * Двойной год: ``1750/51``, ``1700/01``. Возвращается **второй** год
      (New Style) — так его представляют большинство современных
      генеалогических систем.
    * Лидирующие нули — допустимы (``0850`` → ``850``).

    Raises:
        ValueError: На пустой/нечитаемой строке.
    """
    if not token:
        msg = "Empty year token"
        raise ValueError(msg)

    m = _DUAL_YEAR_RE.match(token)
    if m is not None:
        first = int(m.group(1))
        second_two_digits = int(m.group(2))
        # Восстанавливаем полный год: века от first, последние 2 цифры — second.
        century = first - (first % 100)
        if second_two_digits < first % 100:
            # Перекатились через век: 1799/00 → 1800.
            century += 100
        return century + second_two_digits

    if _PLAIN_YEAR_RE.match(token) is None:
        msg = f"Cannot parse year token {token!r}"
        raise ValueError(msg)
    return int(token)


def _parse_month_token(token: str, calendar: Calendar) -> int | None:
    """Распознать месяц по календарю. Возвращает индекс месяца или ``None``.

    Возвращаемые индексы — внутреннее представление каждого календаря:

    * Gregorian / Julian: 1..12 (JAN..DEC).
    * Hebrew: 1..13 в религиозном порядке (Nisan=1, Tishri=7, Adar Bet=13).
    * French Republican: 1..13 (Vendémiaire=1, Sansculottides=13).

    Возвращает ``None``, если токен не распознан — это сигнал верхнему
    парсеру, что строка не соответствует форме ``MONTH YEAR``.
    """
    upper = token.upper()
    if calendar in ("gregorian", "julian"):
        return _GREGORIAN_MONTHS.get(upper)
    if calendar == "hebrew":
        return _HEBREW_MONTH_MAP.get(upper)
    if calendar == "french-r":
        return _FRENCH_MONTH_MAP.get(upper)
    return None


def _safe_date(year: int, month: int, day: int, calendar: Calendar) -> date | None:
    """Безопасно построить ``datetime.date`` после конверсии в Gregorian.

    Возвращает ``None``, если результат не помещается в диапазон
    ``datetime.date`` (1..9999) или компоненты невалидны для календаря.
    Для Roman / Unknown — всегда ``None`` (нет конверсии).
    """
    try:
        if calendar == "julian":
            gy, gm, gd = julian_to_gregorian(year, month, day)
        elif calendar == "gregorian":
            gy, gm, gd = year, month, day
        elif calendar == "hebrew":
            # Adar Bet (m=13) существует только в високосный год; convertdate
            # сам по себе это не проверяет — рулим явно.
            if month == 13 and not _hebrew.leap(year):
                return None
            gy, gm, gd = hebrew_to_gregorian(year, month, day)
        elif calendar == "french-r":
            gy, gm, gd = french_republican_to_gregorian(year, month, day)
        else:
            return None

        if gy < 1 or gy > 9999:
            return None
        return date(gy, gm, gd)
    except (ValueError, OverflowError, KeyError):
        # KeyError — convertdate иногда поднимает на невалидных компонентах.
        return None


def _day_before(d: date | None) -> date | None:
    """Вернуть день, предшествующий ``d`` (для bracketing'а до начала след. года)."""
    if d is None:
        return None
    try:
        return date.fromordinal(d.toordinal() - 1)
    except (ValueError, OverflowError):
        return None


def _bracket_year(year: int, calendar: Calendar) -> tuple[date | None, date | None]:
    """Год → bracketing в Gregorian.

    * Gregorian / Julian: ``[Jan 1, Dec 31]`` соответствующего календаря.
    * Hebrew: гражданский год — ``[Tishri 1 X, день перед Tishri 1 X+1]``.
      Это значит, что Hebrew-год X охватывает осень (X-1) → осень X в Greg.
    * French Republican: ``[Vendémiaire 1 X, день перед Vendémiaire 1 X+1]``.
    """
    if calendar == "gregorian":
        return _safe_date(year, 1, 1, "gregorian"), _safe_date(year, 12, 31, "gregorian")
    if calendar == "julian":
        return _safe_date(year, 1, 1, "julian"), _safe_date(year, 12, 31, "julian")
    if calendar == "hebrew":
        lower = _safe_date(year, 7, 1, "hebrew")
        upper = _day_before(_safe_date(year + 1, 7, 1, "hebrew"))
        return lower, upper
    if calendar == "french-r":
        lower = _safe_date(year, 1, 1, "french-r")
        upper = _day_before(_safe_date(year + 1, 1, 1, "french-r"))
        return lower, upper
    return None, None


def _bracket_month(year: int, month: int, calendar: Calendar) -> tuple[date | None, date | None]:
    """Месяц → bracketing ``[1-е, последний день]`` в Gregorian."""
    if calendar == "gregorian":
        last_day = _days_in_gregorian_month(year, month)
    elif calendar == "julian":
        last_day = _days_in_julian_month(year, month)
    elif calendar == "hebrew":
        try:
            last_day = _hebrew.month_length(year, month)
        except (ValueError, KeyError):
            return None, None
    elif calendar == "french-r":
        if 1 <= month <= 12:
            last_day = 30
        elif month == 13:
            try:
                last_day = 6 if _french_republican.leap(year) else 5
            except (ValueError, KeyError):
                return None, None
        else:
            return None, None
    else:
        return None, None
    lower = _safe_date(year, month, 1, calendar)
    upper = _safe_date(year, month, last_day, calendar)
    return lower, upper


def _bracket_exact(
    year: int, month: int, day: int, calendar: Calendar
) -> tuple[date | None, date | None]:
    """Точная дата → bracketing ``[d, d]`` в Gregorian."""
    d = _safe_date(year, month, day, calendar)
    return d, d


def _parse_single_date(text: str, calendar: Calendar) -> tuple[date | None, date | None]:
    """Распарсить одну дату внутри уже определённого календаря.

    Допустимые формы (после strip):

    * ``YEAR``                  → bracketing ``[Jan 1, Dec 31]``
    * ``MONTH YEAR``            → bracketing ``[1, last day]``
    * ``DAY MONTH YEAR``        → ``[d, d]``
    * любой из выше + ``BC``    → bracketing ``(None, None)``

    Raises:
        GedcomDateParseError: При нераспознаваемых компонентах.
    """
    parts = text.split()
    if not parts:
        msg = "Empty date components"
        raise GedcomDateParseError(msg)

    # BC / B.C. суффикс — даты до н.э., bracketing невозможен (за пределами date).
    is_bc = False
    if parts[-1].upper() in ("BC", "B.C."):
        is_bc = True
        parts = parts[:-1]
        if not parts:
            msg = "BC suffix without year"
            raise GedcomDateParseError(msg)

    if len(parts) == 1:
        try:
            year = _parse_year_token(parts[0])
        except ValueError as e:
            raise GedcomDateParseError(str(e)) from e
        if is_bc:
            return None, None
        return _bracket_year(year, calendar)

    if len(parts) == 2:
        month = _parse_month_token(parts[0], calendar)
        if month is None:
            msg = f"Unknown month token {parts[0]!r} for calendar {calendar}"
            raise GedcomDateParseError(msg)
        try:
            year = _parse_year_token(parts[1])
        except ValueError as e:
            raise GedcomDateParseError(str(e)) from e
        if is_bc:
            return None, None
        return _bracket_month(year, month, calendar)

    if len(parts) == 3:
        if not parts[0].isdigit():
            msg = f"Day must be a number, got {parts[0]!r}"
            raise GedcomDateParseError(msg)
        day = int(parts[0])
        month = _parse_month_token(parts[1], calendar)
        if month is None:
            msg = f"Unknown month token {parts[1]!r} for calendar {calendar}"
            raise GedcomDateParseError(msg)
        try:
            year = _parse_year_token(parts[2])
        except ValueError as e:
            raise GedcomDateParseError(str(e)) from e
        if is_bc:
            return None, None
        result = _bracket_exact(year, month, day, calendar)
        if result == (None, None) and calendar in _CALENDARS_WITH_BRACKETING:
            # Календарь поддерживается, но компоненты невалидны (32 янв и т.п.).
            msg = f"Invalid date components: {text!r} ({calendar})"
            raise GedcomDateParseError(msg)
        return result

    msg = f"Date has too many tokens ({len(parts)}): {text!r}"
    raise GedcomDateParseError(msg)


# -----------------------------------------------------------------------------
# Календарный escape
# -----------------------------------------------------------------------------

_CALENDAR_ESCAPE_RE: re.Pattern[str] = re.compile(r"^@#D[A-Z]+( [A-Z]+)?@\s*", re.ASCII)


def _strip_calendar_escape(text: str) -> tuple[Calendar, str]:
    """Снять calendar escape со строки. Возвращает ``(календарь, остаток)``.

    Если escape отсутствует — возвращает ``("gregorian", text)`` (default).
    Неизвестный escape — возвращает ``("unknown", остаток)``.
    """
    m = _CALENDAR_ESCAPE_RE.match(text)
    if m is None:
        return "gregorian", text
    escape = m.group(0).rstrip()
    rest = text[m.end() :]
    calendar = _CALENDAR_ESCAPE_MAP.get(escape, "unknown")
    return calendar, rest


# -----------------------------------------------------------------------------
# Top-level
# -----------------------------------------------------------------------------


def _shift_date(d: date | None, days: int) -> date | None:
    """Сдвинуть дату на ``days`` дней (может быть отрицательным).

    Возвращает ``None``, если результат вышел за пределы ``datetime.date``.
    """
    if d is None:
        return None
    try:
        return date.fromordinal(d.toordinal() + days)
    except (ValueError, OverflowError):
        return None


def _split_keyword(text: str, keyword: str) -> tuple[str, str] | None:
    """Найти токен ``keyword`` в тексте (как отдельное слово).

    Возвращает ``(до, после)`` или ``None``, если не найдено.
    Регистронезависимо. Пробельные разделители съедаются.
    """
    pattern = rf"\s+{re.escape(keyword)}\s+"
    m = re.search(pattern, text, re.IGNORECASE)
    if m is None:
        return None
    return text[: m.start()], text[m.end() :]


def parse_gedcom_date(value: str) -> ParsedDate:
    """Разобрать значение тега DATE в :class:`ParsedDate`.

    Args:
        value: Сырое значение (например, ``"ABT 1850"``, ``"BET 1840 AND 1850"``,
            ``"@#DJULIAN@ 5 MAR 1812"``, ``"INT 1900 (about Christmas)"``).

    Returns:
        Заполненный :class:`ParsedDate`. Поле ``raw`` — исходная строка
        (без strip), остальные — структурный разбор.

    Raises:
        GedcomDateParseError: Если строка не разобралась ни в одну форму.
    """
    raw = value
    s = value.strip()
    if not s:
        msg = "Empty date string"
        raise GedcomDateParseError(msg)

    # Чистая phrase form: "(text)".
    if s.startswith("(") and s.endswith(")") and len(s) >= 2:
        return ParsedDate(raw=raw, qualifier="none", phrase=s[1:-1])

    # INT date (phrase) — INT может быть с любым календарным escape после
    # самого "INT", но на практике GEDCOM пишут escape перед всей датой.
    upper = s.upper()
    if upper == "INT":
        msg = f"INT without date: {raw!r}"
        raise GedcomDateParseError(msg)
    if upper.startswith("INT "):
        rest = s[4:].strip()
        # Извлекаем (phrase) с конца, если есть.
        phrase: str | None = None
        if rest.endswith(")"):
            paren_open = rest.rfind("(")
            if paren_open != -1:
                phrase = rest[paren_open + 1 : -1]
                rest = rest[:paren_open].strip()
        calendar, date_text = _strip_calendar_escape(rest)
        if not date_text:
            # INT без даты — допустимо? GEDCOM требует <DATE>; считаем ошибкой.
            msg = f"INT without date: {raw!r}"
            raise GedcomDateParseError(msg)
        lower, upper_dt = _parse_single_date(date_text, calendar)
        return ParsedDate(
            raw=raw,
            calendar=calendar,
            qualifier="INT",
            date_lower=lower,
            date_upper=upper_dt,
            phrase=phrase,
        )

    # Остальные формы могут начинаться с calendar escape.
    calendar, body = _strip_calendar_escape(s)
    body = body.strip()
    body_upper = body.upper()
    if not body:
        msg = f"Date has only calendar escape, no value: {raw!r}"
        raise GedcomDateParseError(msg)

    # Period: FROM .. TO ..
    if body_upper.startswith("FROM "):
        rest = body[5:].strip()
        split_to = _split_keyword(rest, "TO")
        if split_to is not None:
            from_part, to_part = split_to
            from_lower, _ = _parse_single_date(from_part.strip(), calendar)
            _, to_upper = _parse_single_date(to_part.strip(), calendar)
            return ParsedDate(
                raw=raw,
                calendar=calendar,
                is_period=True,
                date_lower=from_lower,
                date_upper=to_upper,
            )
        # Только FROM x — открытый правый край.
        from_lower, _ = _parse_single_date(rest, calendar)
        return ParsedDate(
            raw=raw,
            calendar=calendar,
            is_period=True,
            date_lower=from_lower,
        )

    if body_upper.startswith("TO "):
        rest = body[3:].strip()
        _, to_upper = _parse_single_date(rest, calendar)
        return ParsedDate(
            raw=raw,
            calendar=calendar,
            is_period=True,
            date_upper=to_upper,
        )

    # Range: BET .. AND ..
    if body_upper.startswith("BET "):
        rest = body[4:].strip()
        split_and = _split_keyword(rest, "AND")
        if split_and is None:
            msg = f"BET without AND: {raw!r}"
            raise GedcomDateParseError(msg)
        bet_part, and_part = split_and
        bet_lower, _ = _parse_single_date(bet_part.strip(), calendar)
        _, and_upper = _parse_single_date(and_part.strip(), calendar)
        return ParsedDate(
            raw=raw,
            calendar=calendar,
            is_range=True,
            date_lower=bet_lower,
            date_upper=and_upper,
        )

    # Открытые границы: BEF / AFT — строгое сравнение.
    if body_upper.startswith("BEF "):
        rest = body[4:].strip()
        lower_b, _ = _parse_single_date(rest, calendar)
        return ParsedDate(
            raw=raw,
            calendar=calendar,
            qualifier="BEF",
            date_upper=_shift_date(lower_b, -1),
        )

    if body_upper.startswith("AFT "):
        rest = body[4:].strip()
        _, upper_a = _parse_single_date(rest, calendar)
        return ParsedDate(
            raw=raw,
            calendar=calendar,
            qualifier="AFT",
            date_lower=_shift_date(upper_a, +1),
        )

    # Приблизительные: ABT / CAL / EST.
    for q in ("ABT", "CAL", "EST"):
        if body_upper.startswith(f"{q} "):
            rest = body[len(q) + 1 :].strip()
            lower, upper_dt = _parse_single_date(rest, calendar)
            return ParsedDate(
                raw=raw,
                calendar=calendar,
                qualifier=q,
                date_lower=lower,
                date_upper=upper_dt,
            )

    # Иначе — простая дата.
    lower, upper_dt = _parse_single_date(body, calendar)
    return ParsedDate(
        raw=raw,
        calendar=calendar,
        date_lower=lower,
        date_upper=upper_dt,
    )


__all__ = [
    "Calendar",
    "ParsedDate",
    "Qualifier",
    "julian_to_gregorian",
    "parse_gedcom_date",
]
