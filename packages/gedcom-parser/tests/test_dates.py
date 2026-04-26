"""Тесты модуля ``gedcom_parser.dates``: разбор и нормализация дат."""

from __future__ import annotations

import warnings
from datetime import date

import pytest
from gedcom_parser.dates import ParsedDate, julian_to_gregorian, parse_gedcom_date
from gedcom_parser.entities import Event, Header
from gedcom_parser.exceptions import GedcomDateParseError, GedcomDateWarning
from gedcom_parser.parser import parse_text
from pydantic import ValidationError

# -----------------------------------------------------------------------------
# Простые точные даты в Gregorian
# -----------------------------------------------------------------------------


class TestGregorianExact:
    def test_year_only(self) -> None:
        r = parse_gedcom_date("1850")
        assert r.calendar == "gregorian"
        assert r.qualifier == "none"
        assert r.is_period is False
        assert r.is_range is False
        assert r.date_lower == date(1850, 1, 1)
        assert r.date_upper == date(1850, 12, 31)

    def test_month_year(self) -> None:
        r = parse_gedcom_date("FEB 1900")
        assert r.date_lower == date(1900, 2, 1)
        assert r.date_upper == date(1900, 2, 28)

    def test_month_year_leap_year(self) -> None:
        r = parse_gedcom_date("FEB 2000")
        assert r.date_upper == date(2000, 2, 29)

    def test_month_year_century_non_leap(self) -> None:
        # 1900 не високосный по правилу %100 без %400.
        r = parse_gedcom_date("FEB 1900")
        assert r.date_upper == date(1900, 2, 28)

    def test_day_month_year(self) -> None:
        r = parse_gedcom_date("5 JUN 1850")
        assert r.date_lower == date(1850, 6, 5)
        assert r.date_upper == date(1850, 6, 5)

    def test_lowercase_month(self) -> None:
        # GEDCOM-спека требует uppercase, но реальные файлы вариативны.
        r = parse_gedcom_date("5 jun 1850")
        assert r.date_lower == date(1850, 6, 5)

    def test_short_year(self) -> None:
        r = parse_gedcom_date("850")
        assert r.date_lower == date(850, 1, 1)

    def test_dual_year_basic(self) -> None:
        # Old/New Style: 1750/51 → используем 1751 (New Style).
        r = parse_gedcom_date("20 FEB 1750/51")
        assert r.date_lower == date(1751, 2, 20)

    def test_dual_year_century_rollover(self) -> None:
        # 1799/00 → 1800.
        r = parse_gedcom_date("1799/00")
        assert r.date_lower == date(1800, 1, 1)
        assert r.date_upper == date(1800, 12, 31)


class TestRawPreserved:
    def test_raw_kept_verbatim(self) -> None:
        # raw сохраняет даже окружающие пробелы, чтобы writer мог восстановить.
        raw = "  ABT  1850  "
        r = parse_gedcom_date(raw)
        assert r.raw == raw


# -----------------------------------------------------------------------------
# Quantifier'ы и границы
# -----------------------------------------------------------------------------


class TestApproximated:
    @pytest.mark.parametrize("q", ["ABT", "CAL", "EST"])
    def test_qualifier(self, q: str) -> None:
        r = parse_gedcom_date(f"{q} 1850")
        assert r.qualifier == q
        assert r.date_lower == date(1850, 1, 1)
        assert r.date_upper == date(1850, 12, 31)

    def test_qualifier_lowercase(self) -> None:
        r = parse_gedcom_date("abt 1850")
        assert r.qualifier == "ABT"


class TestBefAft:
    def test_bef_year_strict(self) -> None:
        # BEF — строго до начала диапазона.
        r = parse_gedcom_date("BEF 1850")
        assert r.qualifier == "BEF"
        assert r.date_lower is None
        assert r.date_upper == date(1849, 12, 31)

    def test_aft_year_strict(self) -> None:
        # AFT — строго после конца диапазона.
        r = parse_gedcom_date("AFT 1850")
        assert r.qualifier == "AFT"
        assert r.date_lower == date(1851, 1, 1)
        assert r.date_upper is None

    def test_bef_exact_date(self) -> None:
        r = parse_gedcom_date("BEF 5 JUN 1850")
        assert r.date_upper == date(1850, 6, 4)

    def test_aft_exact_date(self) -> None:
        r = parse_gedcom_date("AFT 5 JUN 1850")
        assert r.date_lower == date(1850, 6, 6)


# -----------------------------------------------------------------------------
# Range (BET..AND) и Period (FROM..TO)
# -----------------------------------------------------------------------------


class TestRange:
    def test_bet_and(self) -> None:
        r = parse_gedcom_date("BET 1840 AND 1850")
        assert r.is_range is True
        assert r.is_period is False
        assert r.date_lower == date(1840, 1, 1)
        assert r.date_upper == date(1850, 12, 31)

    def test_bet_and_with_exact_dates(self) -> None:
        r = parse_gedcom_date("BET 5 JUN 1840 AND 5 JUN 1850")
        assert r.date_lower == date(1840, 6, 5)
        assert r.date_upper == date(1850, 6, 5)

    def test_bet_without_and_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="without AND"):
            parse_gedcom_date("BET 1850")


class TestPeriod:
    def test_from_to(self) -> None:
        r = parse_gedcom_date("FROM 1840 TO 1850")
        assert r.is_period is True
        assert r.is_range is False
        assert r.date_lower == date(1840, 1, 1)
        assert r.date_upper == date(1850, 12, 31)

    def test_from_only(self) -> None:
        r = parse_gedcom_date("FROM 1840")
        assert r.is_period is True
        assert r.date_lower == date(1840, 1, 1)
        assert r.date_upper is None

    def test_to_only(self) -> None:
        r = parse_gedcom_date("TO 1850")
        assert r.is_period is True
        assert r.date_lower is None
        assert r.date_upper == date(1850, 12, 31)


# -----------------------------------------------------------------------------
# Phrase form и INT
# -----------------------------------------------------------------------------


class TestPhraseAndInt:
    def test_pure_phrase(self) -> None:
        r = parse_gedcom_date("(circa Christmas 1850)")
        assert r.qualifier == "none"
        assert r.phrase == "circa Christmas 1850"
        assert r.date_lower is None
        assert r.date_upper is None

    def test_int_with_phrase(self) -> None:
        r = parse_gedcom_date("INT 1900 (about Christmas)")
        assert r.qualifier == "INT"
        assert r.phrase == "about Christmas"
        assert r.date_lower == date(1900, 1, 1)
        assert r.date_upper == date(1900, 12, 31)

    def test_int_without_phrase(self) -> None:
        r = parse_gedcom_date("INT 1900")
        assert r.qualifier == "INT"
        assert r.phrase is None
        assert r.date_lower == date(1900, 1, 1)


# -----------------------------------------------------------------------------
# BC и крайние годы
# -----------------------------------------------------------------------------


class TestBcAndOutOfRange:
    @pytest.mark.parametrize("suffix", ["BC", "B.C."])
    def test_year_bc(self, suffix: str) -> None:
        r = parse_gedcom_date(f"50 {suffix}")
        # BC за пределами datetime.date — bracketing None, остальное живёт.
        assert r.calendar == "gregorian"
        assert r.date_lower is None
        assert r.date_upper is None
        assert r.raw == f"50 {suffix}"

    def test_bc_with_full_components(self) -> None:
        r = parse_gedcom_date("1 JAN 100 BC")
        assert r.date_lower is None
        assert r.date_upper is None


# -----------------------------------------------------------------------------
# Календари
# -----------------------------------------------------------------------------


class TestJulianCalendar:
    def test_julian_simple_year(self) -> None:
        r = parse_gedcom_date("@#DJULIAN@ 1582")
        assert r.calendar == "julian"
        # 1 Jan 1582 (Julian) → 11 Jan 1582 (Gregorian).
        assert r.date_lower == date(1582, 1, 11)

    def test_julian_calendar_reform_date(self) -> None:
        # Знаменитая граница: 4 Oct 1582 Julian = 14 Oct 1582 Gregorian.
        # Это была последняя дата Julian перед переходом на Gregorian.
        r = parse_gedcom_date("@#DJULIAN@ 4 OCT 1582")
        assert r.date_lower == date(1582, 10, 14)
        assert r.date_upper == date(1582, 10, 14)

    def test_julian_jan_1900_shift_12_days(self) -> None:
        # До 1 марта 1900 расхождение Julian↔Gregorian ещё 12 дней
        # (1900 — НЕ високосный по Gregorian, так что +13-й день добавляется
        # с 1 марта 1900). Это тонкость, которую важно ловить тестом.
        r = parse_gedcom_date("@#DJULIAN@ 1 JAN 1900")
        assert r.date_lower == date(1900, 1, 13)

    def test_julian_after_march_1900_shift_13_days(self) -> None:
        r = parse_gedcom_date("@#DJULIAN@ 1 MAR 1900")
        assert r.date_lower == date(1900, 3, 14)

    def test_julian_with_qualifier(self) -> None:
        r = parse_gedcom_date("@#DJULIAN@ ABT 5 JUN 1812")
        # @#DJULIAN@ выносится первым, ABT обрабатывается на остатке.
        assert r.calendar == "julian"
        assert r.qualifier == "ABT"

    def test_julian_to_gregorian_helper(self) -> None:
        # Прямая проверка низкоуровневого хелпера. Ключевые точки:
        #   - 4 Oct 1582: последний день перед календарной реформой → 10 дней.
        #   - 1 Jan 1900: ещё 12 дней (до Feb 29 1900 в Julian).
        #   - 1 Jan 2000: 13 дней (стабильный shift с марта 1900 по март 2100).
        assert julian_to_gregorian(1582, 10, 4) == (1582, 10, 14)
        assert julian_to_gregorian(1900, 1, 1) == (1900, 1, 13)
        assert julian_to_gregorian(2000, 1, 1) == (2000, 1, 14)


class TestRomanAndUnknownCalendars:
    """Roman / Unknown — конверсии нет, bracketing всегда None."""

    def test_unknown_calendar(self) -> None:
        r = parse_gedcom_date("@#DUNKNOWN@ 1850")
        assert r.calendar == "unknown"
        assert r.date_lower is None
        assert r.date_upper is None

    def test_roman_calendar(self) -> None:
        r = parse_gedcom_date("@#DROMAN@ 1850")
        assert r.calendar == "roman"
        assert r.date_lower is None


class TestHebrewCalendar:
    """Hebrew (convertdate, религиозная нумерация месяцев, гражданский год)."""

    def test_exact_date_nisan(self) -> None:
        # 1 Nisan 5780 = 26 March 2020 (через convertdate).
        r = parse_gedcom_date("@#DHEBREW@ 1 NSN 5780")
        assert r.calendar == "hebrew"
        assert r.date_lower == date(2020, 3, 26)
        assert r.date_upper == date(2020, 3, 26)

    def test_exact_date_tishri_starts_civil_year(self) -> None:
        # 1 Tishri 5780 = 30 September 2019 (Rosh Hashanah).
        r = parse_gedcom_date("@#DHEBREW@ 1 TSH 5780")
        assert r.date_lower == date(2019, 9, 30)

    def test_year_bracketing_civil_span(self) -> None:
        # Hebrew civil год 5780: Tishri 1 5780 → Elul 29 5780.
        # = 30 Sep 2019 → 18 Sep 2020.
        r = parse_gedcom_date("@#DHEBREW@ 5780")
        assert r.date_lower == date(2019, 9, 30)
        assert r.date_upper == date(2020, 9, 18)

    def test_month_bracketing(self) -> None:
        # Tishri 5780 — 30 дней.
        r = parse_gedcom_date("@#DHEBREW@ TSH 5780")
        assert r.date_lower == date(2019, 9, 30)
        # Конец Tishri 5780 — 29 Oct 2019.
        assert r.date_upper == date(2019, 10, 29)

    def test_adar_bet_in_leap_year(self) -> None:
        # Hebrew 5779 — високосный, Adar Bet есть. 1 Adar Bet 5779 валидно.
        r = parse_gedcom_date("@#DHEBREW@ 1 ADS 5779")
        assert r.date_lower is not None
        # Без leap year ADS невалидно — bracket вернёт None.

    def test_adar_bet_in_non_leap_year_raises(self) -> None:
        # Hebrew 5781 — не високосный, Adar Bet (m=13) невалидно.
        # convertdate сам по себе тут «прокатывает» в Nisan/Adar следующего
        # года; мы поднимаем ошибку, чтобы каллер увидел проблему.
        with pytest.raises(GedcomDateParseError, match="Invalid date components"):
            parse_gedcom_date("@#DHEBREW@ 1 ADS 5781")


class TestFrenchRepublicanCalendar:
    """French Republican (convertdate). Год 1 = 22 Sep 1792."""

    def test_year_1_starts_22_sep_1792(self) -> None:
        r = parse_gedcom_date("@#DFRENCH R@ 1 VEND 1")
        assert r.calendar == "french-r"
        assert r.date_lower == date(1792, 9, 22)

    def test_year_bracketing(self) -> None:
        r = parse_gedcom_date("@#DFRENCH R@ 1")
        assert r.date_lower == date(1792, 9, 22)
        # Год 1 — не високосный, Sansculottides 5 дней.
        # Кончается 21 Sep 1793 (день перед Vendémiaire 1, 2).
        assert r.date_upper == date(1793, 9, 21)

    def test_month_bracketing_30_day_month(self) -> None:
        # Vendémiaire 1 года 1: 22 Sep 1792 → 21 Oct 1792 (30 дней).
        r = parse_gedcom_date("@#DFRENCH R@ VEND 1")
        assert r.date_lower == date(1792, 9, 22)
        assert r.date_upper == date(1792, 10, 21)

    def test_sansculottides_non_leap_5_days(self) -> None:
        # Год 1 — не високосный, COMP = 5 дней.
        r = parse_gedcom_date("@#DFRENCH R@ COMP 1")
        assert r.date_lower is not None
        assert r.date_upper is not None
        # 17 Sep 1793 (Sansculottides 1) до 21 Sep 1793 (Sansculottides 5).
        assert (r.date_upper - r.date_lower).days == 4

    def test_sansculottides_leap_year_6_days(self) -> None:
        # Год 3 — високосный, COMP = 6 дней.
        r = parse_gedcom_date("@#DFRENCH R@ COMP 3")
        assert r.date_lower is not None
        assert r.date_upper is not None
        assert (r.date_upper - r.date_lower).days == 5


# -----------------------------------------------------------------------------
# Ошибки
# -----------------------------------------------------------------------------


class TestErrors:
    def test_empty_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="Empty"):
            parse_gedcom_date("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="Empty"):
            parse_gedcom_date("   ")

    def test_calendar_escape_only_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="only calendar escape"):
            parse_gedcom_date("@#DGREGORIAN@")

    def test_int_without_date_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="INT without date"):
            parse_gedcom_date("INT")

    def test_unknown_month_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="Unknown month"):
            parse_gedcom_date("FOO 1850")

    def test_invalid_day_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="Invalid date"):
            parse_gedcom_date("32 JAN 1850")

    def test_too_many_tokens_raises(self) -> None:
        with pytest.raises(GedcomDateParseError, match="too many tokens"):
            parse_gedcom_date("1 2 3 4 5")

    def test_garbage_year_raises(self) -> None:
        with pytest.raises(GedcomDateParseError):
            parse_gedcom_date("ABCD")


# -----------------------------------------------------------------------------
# Расширенные ("fuzzy") формы дат, не описанные GEDCOM-спекой, но встречающиеся
# в реальных файлах (MyHeritage, FamilySearch, ручной ввод). Парсер обрабатывает
# их в lenient-режиме, чтобы corpus-смок-тесты на чужих GED не падали.
# -----------------------------------------------------------------------------


class TestFuzzyDateForms:
    """Расширенные формы одиночного года-токена через _parse_single_year_token."""

    # ---- Фуззи десятилетие/век: подчёркивания вместо неизвестных цифр ---------

    def test_fuzzy_decade_three_digits_underscore(self) -> None:
        # "198_" → весь диапазон 1980..1989.
        r = parse_gedcom_date("198_")
        assert r.calendar == "gregorian"
        assert r.qualifier == "none"
        assert r.is_period is False
        assert r.is_range is False
        assert r.date_lower == date(1980, 1, 1)
        assert r.date_upper == date(1989, 12, 31)

    def test_fuzzy_century_two_digits_underscore(self) -> None:
        # "18__" → весь XIX век (1800..1899).
        r = parse_gedcom_date("18__")
        assert r.date_lower == date(1800, 1, 1)
        assert r.date_upper == date(1899, 12, 31)

    def test_fuzzy_decade_preserves_raw(self) -> None:
        # raw сохраняется для round-trip writer'а.
        r = parse_gedcom_date("197_")
        assert r.raw == "197_"

    # ---- Год+месяц слитно (MyHeritage exports): YYYYMM -----------------------

    def test_year_month_concatenated_february_leap(self) -> None:
        # "202002" → февраль 2020 (високосный год → 29 дней).
        r = parse_gedcom_date("202002")
        assert r.date_lower == date(2020, 2, 1)
        assert r.date_upper == date(2020, 2, 29)

    def test_year_month_concatenated_february_non_leap(self) -> None:
        # "190002" → февраль 1900 (НЕ високосный, %100 без %400 → 28 дней).
        r = parse_gedcom_date("190002")
        assert r.date_upper == date(1900, 2, 28)

    def test_year_month_concatenated_july(self) -> None:
        # "194207" → июль 1942 → 31 день.
        r = parse_gedcom_date("194207")
        assert r.date_lower == date(1942, 7, 1)
        assert r.date_upper == date(1942, 7, 31)

    def test_year_month_concatenated_invalid_month_raises(self) -> None:
        # "994199" — месяц 99 невалидный, ни одна из веток не подберёт.
        with pytest.raises(GedcomDateParseError):
            parse_gedcom_date("994199")

    # ---- Месяц/год через слэш: M/YYYY или MM/YYYY ----------------------------

    def test_month_year_slash_october(self) -> None:
        # "10/1941" → октябрь 1941.
        r = parse_gedcom_date("10/1941")
        assert r.date_lower == date(1941, 10, 1)
        assert r.date_upper == date(1941, 10, 31)

    def test_month_year_slash_single_digit_month(self) -> None:
        # "5/1850" → май 1850.
        r = parse_gedcom_date("5/1850")
        assert r.date_lower == date(1850, 5, 1)
        assert r.date_upper == date(1850, 5, 31)

    def test_dual_year_not_misparsed_as_month_slash(self) -> None:
        # "1750/51" — это GEDCOM dual-year (юлианский/григорианский переход),
        # _parse_single_year_token должен сначала распознать dual-year и
        # НЕ скатываться в _MONTH_YEAR_SLASH_RE (где первый токен > 12 = invalid).
        r = parse_gedcom_date("1750/51")
        # Главное — что парсинг не упал и год около 1750-1751.
        assert r.date_lower is not None
        assert r.date_lower.year in (1750, 1751)

    def test_month_year_slash_invalid_month_raises(self) -> None:
        # "13/1941" — месяц 13 невалидный, _parse_year_token тоже не справится
        # (вторая часть из 4 цифр не похожа на dual-year).
        with pytest.raises(GedcomDateParseError):
            parse_gedcom_date("13/1941")


class TestFuzzyRangeForms:
    """Расширенные формы диапазонов через _YEAR_RANGE_RE и _OR_RE,
    обрабатываемые на верхнем уровне ``parse_gedcom_date``."""

    # ---- Год-диапазон через дефис/en-dash/em-dash ----------------------------

    def test_year_range_hyphen(self) -> None:
        r = parse_gedcom_date("1985-2020")
        assert r.is_range is True
        assert r.date_lower == date(1985, 1, 1)
        assert r.date_upper == date(2020, 12, 31)

    def test_year_range_en_dash(self) -> None:
        # En-dash U+2013 — типичен для копий из Wikipedia / Word-документов.
        r = parse_gedcom_date("1820 – 1830")
        assert r.is_range is True
        assert r.date_lower == date(1820, 1, 1)
        assert r.date_upper == date(1830, 12, 31)

    def test_year_range_em_dash(self) -> None:
        # Em-dash U+2014 — встречается в русскоязычных файлах.
        r = parse_gedcom_date("1820 — 1830")
        assert r.is_range is True
        assert r.date_lower == date(1820, 1, 1)
        assert r.date_upper == date(1830, 12, 31)

    def test_year_range_no_spaces(self) -> None:
        # "1820-1830" — без пробелов вокруг дефиса.
        r = parse_gedcom_date("1820-1830")
        assert r.is_range is True
        assert r.date_lower == date(1820, 1, 1)
        assert r.date_upper == date(1830, 12, 31)

    def test_bet_with_dash_fallback(self) -> None:
        # "BET 1820-1830" — стандартный BET без AND, lenient fallback на
        # _YEAR_RANGE_RE внутри BET-ветки.
        r = parse_gedcom_date("BET 1820-1830")
        assert r.is_range is True
        assert r.date_lower == date(1820, 1, 1)
        assert r.date_upper == date(1830, 12, 31)

    # ---- Альтернатива через "or" --------------------------------------------

    def test_or_alternative_lowercase(self) -> None:
        r = parse_gedcom_date("1870 or 1875")
        assert r.is_range is True
        assert r.date_lower == date(1870, 1, 1)
        assert r.date_upper == date(1875, 12, 31)

    def test_or_alternative_uppercase(self) -> None:
        r = parse_gedcom_date("1870 OR 1875")
        assert r.is_range is True
        assert r.date_lower == date(1870, 1, 1)
        assert r.date_upper == date(1875, 12, 31)

    def test_or_alternative_mixed_case(self) -> None:
        r = parse_gedcom_date("1870 Or 1875")
        assert r.is_range is True
        assert r.date_lower == date(1870, 1, 1)
        assert r.date_upper == date(1875, 12, 31)


class TestFuzzyTildeApproximation:
    """Тильда ``~`` как маркер приблизительности (lenient синоним ABT)."""

    def test_tilde_year_treated_as_abt(self) -> None:
        # "~1850" должно вести себя как "ABT 1850".
        r = parse_gedcom_date("~1850")
        assert r.qualifier == "ABT"
        assert r.date_lower == date(1850, 1, 1)
        assert r.date_upper == date(1850, 12, 31)

    def test_tilde_with_fuzzy_decade(self) -> None:
        # "~189_" — комбинация двух lenient-фич: тильда + fuzzy decade.
        r = parse_gedcom_date("~189_")
        assert r.qualifier == "ABT"
        assert r.date_lower == date(1890, 1, 1)
        assert r.date_upper == date(1899, 12, 31)


# -----------------------------------------------------------------------------
# Frozen и extra="forbid"
# -----------------------------------------------------------------------------


class TestFrozen:
    def test_parsed_date_is_frozen(self) -> None:
        r = parse_gedcom_date("1850")
        with pytest.raises(ValidationError):
            r.qualifier = "ABT"  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ParsedDate(raw="1850", unknown="x")  # type: ignore[call-arg]


# -----------------------------------------------------------------------------
# Интеграция в Event и Header
# -----------------------------------------------------------------------------


class TestEventIntegration:
    def test_birt_date_parsed(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 DATE 1 JAN 1850\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.date_raw == "1 JAN 1850"
        assert event.date is not None
        assert event.date.date_lower == date(1850, 1, 1)

    def test_event_without_date(self) -> None:
        text = "0 @I1@ INDI\n1 DEAT\n2 PLAC Vilnius\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("DEAT"))  # type: ignore[arg-type]
        assert event.date_raw is None
        assert event.date is None

    def test_event_unparseable_date_warns_and_keeps_raw(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 DATE FOOBAR 1850\n"
        indi = parse_text(text)[0]
        with pytest.warns(GedcomDateWarning, match="Failed to parse date"):
            event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        # Round-trip всё ещё возможен — date_raw сохранён.
        assert event.date_raw == "FOOBAR 1850"
        assert event.date is None

    def test_event_julian_date(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 DATE @#DJULIAN@ 4 OCT 1582\n"
        indi = parse_text(text)[0]
        event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.date is not None
        assert event.date.calendar == "julian"
        assert event.date.date_lower == date(1582, 10, 14)


class TestHeaderIntegration:
    def test_header_date_parsed(self, minimal_ged_text: str) -> None:
        head = parse_text(minimal_ged_text)[0]
        header = Header.from_record(head)
        assert header.date_raw == "25 APR 2026"
        assert header.date is not None
        assert header.date.date_lower == date(2026, 4, 25)


# -----------------------------------------------------------------------------
# Quiet-режим: warning'и не должны уходить в общий поток если ничего не сломано
# -----------------------------------------------------------------------------


class TestNoSpuriousWarnings:
    def test_clean_event_emits_no_warnings(self) -> None:
        text = "0 @I1@ INDI\n1 BIRT\n2 DATE BET 1840 AND 1850\n"
        indi = parse_text(text)[0]
        with warnings.catch_warnings():
            warnings.simplefilter("error", GedcomDateWarning)
            # Если бы парсинг даты дал warning — pytest упал бы.
            event = Event.from_record(indi.find("BIRT"))  # type: ignore[arg-type]
        assert event.date is not None
        assert event.date.is_range is True
