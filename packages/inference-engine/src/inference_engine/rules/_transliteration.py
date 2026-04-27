"""Internal helper: транслитерация кириллицы → латиницы для phonetic-rules.

Daitch-Mokotoff (см. ``entity_resolution.phonetic.daitch_mokotoff``)
работает на алфавите A–Z. Реальные GEDCOM-файлы из СНГ часто содержат
кириллические имена/места: ``Житницкий``, ``Днепропетровск``. Без
транслитерации DM просто сбрасывает все non-ASCII буквы и фамилии
``Zhitnitzky`` ↔ ``Житницкий`` оказываются в разных bucket'ах
(один → 6-цифровой код, второй → "00000" или близко к нулю).

Применяем упрощённую ISO-9-подобную таблицу: каждая русская / украинская
буква → 1–4 латинских. Это не полноценный ISO-9 (он юридически точнее
с диакритикой), но достаточно для phonetic-key матчинга — DM-bucket'ы
у ``Zhitnitzky`` и транслитерации ``Житницкий`` → ``ZHITNITSKIY`` будут
пересекаться.

Этот helper приватный (``_`` prefix), не часть public API
``inference_engine``. Вынесен в отдельный модуль чтобы две rule
(SurnameMatchRule и BirthPlaceMatchRule) не дублировали таблицу.

Не используем ``gedcom_parser.transliteration`` чтобы не создавать
cross-package dep на пакет с другой ролью (parser → rules).
"""

from __future__ import annotations

# Russian / Ukrainian / Belarusian буквы → латиница.
# Орfortунный compromise: Ж → ZH (передаём шипящую digraph'ой, что
# совпадает со встроенным DM-правилом для ZH → 4).
# Источник набора: ISO 9:1995 упрощённый (без диакритики Ě/Č/Š для
# совместимости с pure-ASCII DM-таблицей в entity_resolution.phonetic).
_CYR_TO_LAT: dict[str, str] = {
    "А": "A",
    "Б": "B",
    "В": "V",
    "Г": "G",
    "Ґ": "G",  # украинская
    "Д": "D",
    "Е": "E",
    "Ё": "YO",
    "Є": "YE",  # украинская
    "Ж": "ZH",
    "З": "Z",
    "И": "I",
    "І": "I",  # украинская / белорусская
    "Ї": "YI",  # украинская
    "Й": "Y",
    "К": "K",
    "Л": "L",
    "М": "M",
    "Н": "N",
    "О": "O",
    "П": "P",
    "Р": "R",
    "С": "S",
    "Т": "T",
    "У": "U",
    "Ў": "U",  # белорусская
    "Ф": "F",
    "Х": "KH",
    "Ц": "TS",
    "Ч": "CH",
    "Ш": "SH",
    "Щ": "SHCH",
    "Ъ": "",
    "Ы": "Y",
    "Ь": "",
    "Э": "E",
    "Ю": "YU",
    "Я": "YA",
    "'": "",  # украинский апостроф
    "ʼ": "",
    "’": "",
}


def transliterate_cyrillic(value: str) -> str:
    """Если строка содержит кириллицу — транслитерировать в латиницу.

    Чисто-латинские строки возвращаются без изменений. Регистр сохраняется
    (заглавные → заглавными, строчные → строчными). Знаки препинания и
    цифры пропускаются как есть.

    Args:
        value: Произвольная строка (имя / фамилия / место).

    Returns:
        Строка с заменёнными кириллическими символами. Без таблицы —
        возвращает копию входа.
    """
    if not value:
        return value
    out: list[str] = []
    for ch in value:
        upper = ch.upper()
        if upper in _CYR_TO_LAT:
            replacement = _CYR_TO_LAT[upper]
            # Сохранить регистр: если исходный символ был строчный —
            # сделать lower у первой буквы replacement'а.
            if ch != upper and replacement:
                out.append(replacement[0].lower() + replacement[1:].lower())
            else:
                out.append(replacement)
        else:
            out.append(ch)
    return "".join(out)


__all__ = ["transliterate_cyrillic"]
