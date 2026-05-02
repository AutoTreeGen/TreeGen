"""Общие CSV-утилиты для match-list-парсеров (Phase 16.3).

Покрывает edge cases, которые во всех 5 платформах одни и те же:

* UTF-8 BOM (Excel save-as).
* Windows-1252 fallback (старые экспорты до 2018).
* Числа с запятой как decimal separator (MyHeritage de_DE export).
* Пустые строки в числовых полях ("" / "—" / "N/A") → None.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

_NULL_SENTINELS: frozenset[str] = frozenset(
    {"", "—", "-", "n/a", "na", "null", "none"},
)


def decode_csv_bytes(payload: bytes) -> str:
    """Декодировать байты в str с автодетектом encoding'а.

    Стратегия: UTF-8 (со срезанием BOM), затем Windows-1252 fallback.
    Не делаем chardet — three-way ambiguity не тот случай, и это
    добавило бы тяжёлую транзитивную зависимость.

    Raises:
        UnicodeDecodeError: Если ни один из двух encoding'ов не подошёл.
    """
    # UTF-8 BOM (\xef\xbb\xbf) — частый артефакт Excel «save as CSV UTF-8».
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("windows-1252")


def read_rows(content: str) -> Iterator[dict[str, Any]]:
    """Прочесть CSV (с заголовком) в DictReader-итератор.

    Делает auto-detect разделителя через ``csv.Sniffer`` (Ancestry
    использует comma, MyHeritage/GEDmatch иногда semicolon в
    locale-specific экспортах). Возвращает каждую row как dict
    с string-ключами; нормализация ключей — на парсере.
    """
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        # Дефолт — comma; sniffer падает на единственной колонке.
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    for row in reader:
        # Прибрать None-ключи (могут появиться, если строка короче header).
        yield {k: v for k, v in row.items() if k is not None}


def parse_optional_float(raw: str | None) -> float | None:
    """Распарсить строку как float; None для пустых / null-sentinel.

    Поддерживает comma-decimal (MyHeritage de_DE: "12,3" → 12.3).
    """
    if raw is None:
        return None
    s = raw.strip().lower()
    if s in _NULL_SENTINELS:
        return None
    # Удалить thousand-separators, нормализовать decimal-comma.
    s = s.replace(" ", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        # «1,234.56» — сначала запятая (thousand), потом точка.
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_optional_int(raw: str | None) -> int | None:
    """Распарсить строку как int; None для пустых / null-sentinel."""
    if raw is None:
        return None
    s = raw.strip().lower()
    if s in _NULL_SENTINELS:
        return None
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return None


def first_present(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Вернуть первое не-пустое значение по списку candidate-ключей.

    Платформы периодически переименовывают колонки между экспортами
    («Total cM» → «TotalCm» → «Shared cM»); парсеры передают сюда
    список синонимов и берут первый match.
    """
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip().lower() in _NULL_SENTINELS:
            continue
        if isinstance(value, str):
            return value
        return str(value)
    return None
