"""Daitch-Mokotoff bucket computation helpers (Phase 4.4.1).

Тонкая обёртка вокруг ``entity_resolution.daitch_mokotoff``:

- транслитерирует кириллицу в латиницу через локальный digraph-table
  (Ж→ZH, Ш→SH, …) до подачи в DM, потому что DM-нормализация в
  entity-resolution оставляет только A-Z и иначе теряет всю кириллицу;
- агрегирует уникальные коды от нескольких имён (BIRTH + AKA + …)
  одной персоны в единый ``list[str]`` для колонки ``persons.surname_dm``.

Почему digraph-table, а не ISO-9 из ``gedcom_parser.transliteration``:
ISO-9 даёт ``Ж → Ž``, ``Ш → Š`` (с диакритикой). DM ``_normalize`` стрипает
не-ASCII буквы → ``Ž`` теряется → ``Žitnickij`` → ``ZITNICKIJ``, что не
матчит DM-правило ``ZH → 4`` для исходного ``Zhitnitzky``. Digraph-таблица
сохраняет фонетику в pure-ASCII и совпадает с DM-правилами для шипящих.

Helper приватный для parser-service (используется import_runner +
search-эндпоинт + backfill-скрипт). При появлении второго consumer'а
имеет смысл промоут в ``entity_resolution`` как public API.
"""

from __future__ import annotations

from collections.abc import Iterable

from entity_resolution import daitch_mokotoff

# Russian / Ukrainian / Belarusian буквы → латинские digraph'ы.
# Каждая буква отображается на 1-4 ASCII-символа, чтобы шипящие (Ж, Ш, Щ,
# Ц, Ч) дали те же DM-bucket'ы, что у латинской транслитерации (ZH, SH, …).
# Источник набора: ISO-9-подобный, упрощённый до pure-ASCII.
_CYR_TO_LAT: dict[str, str] = {
    "А": "A",
    "Б": "B",
    "В": "V",
    "Г": "G",
    "Ґ": "G",
    "Д": "D",
    "Е": "E",
    "Ё": "YO",
    "Є": "YE",
    "Ж": "ZH",
    "З": "Z",
    "И": "I",
    "І": "I",
    "Ї": "YI",
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
    "Ў": "U",
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
    "'": "",
    "ʼ": "",
    "’": "",
}


def transliterate_cyrillic(value: str) -> str:
    """Заменить кириллические символы на латинские digraph'ы.

    Чисто-латинские строки возвращаются без изменений. Регистр сохраняется.
    Не-кириллические символы (цифры, пунктуация) пропускаются как есть.
    """
    if not value:
        return value
    out: list[str] = []
    for ch in value:
        upper = ch.upper()
        if upper in _CYR_TO_LAT:
            replacement = _CYR_TO_LAT[upper]
            if ch != upper and replacement:
                out.append(replacement[0].lower() + replacement[1:].lower())
            else:
                out.append(replacement)
        else:
            out.append(ch)
    return "".join(out)


def compute_dm_buckets(name: str | None) -> list[str]:
    """DM-коды для одного имени (с авто-транслитерацией кириллицы).

    Args:
        name: Произвольная строка-имя или ``None``.

    Returns:
        Список 6-цифровых DM-кодов. Пустой list для пустого / None / 100%
        не-алфавитного входа.
    """
    if not name:
        return []
    # daitch_mokotoff в isolated-env mypy для pre-commit виден как Any
    # (entity-resolution не входит в additional_dependencies хука) —
    # явный list[str] cast снимает no-any-return.
    codes: list[str] = list(daitch_mokotoff(transliterate_cyrillic(name)))
    return codes


def merge_dm_buckets(names: Iterable[str | None]) -> list[str]:
    """Уникальные DM-коды по нескольким именам одной персоны.

    Объединяет результаты ``compute_dm_buckets`` для всех Name-записей
    (BIRTH + AKA + maiden + …). Порядок не важен (массив используется с
    operator ``&&`` arrays overlap), но возвращаем sorted для
    детерминизма в тестах и логах.

    Возвращает пустой list если на вход пришли только пустые / None.
    """
    bucket: set[str] = set()
    for name in names:
        bucket.update(compute_dm_buckets(name))
    return sorted(bucket)
