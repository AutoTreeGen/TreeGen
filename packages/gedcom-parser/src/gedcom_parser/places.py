"""Нормализация мест (ROADMAP §5.1.7).

Структура тега ``PLAC`` в GEDCOM 5.5.5 §3.5:

* Значение тега — иерархия от самого узкого к самому широкому, через
  запятую: ``"Slonim, Grodno Governorate, Russian Empire"``.
* Подтег ``FORM`` (если задан) — шаблон уровней, например
  ``"City, County, State, Country"``. Может стоять и на ``HEAD.PLAC.FORM``
  как умолчание для всего файла.
* Подтег ``MAP`` с дочерними ``LATI`` (``"N51.5074"``) и ``LONG``
  (``"W0.1278"``) — координаты.
* ``FONE`` / ``ROMN`` — фонетический / романизированный варианты
  (как у :class:`gedcom_parser.entities.Name`).

Эта итерация даёт **структурный** разбор PLAC: иерархию уровней,
координаты, варианты, форму. Историческая нормализация (Wilno → Vilnius,
маппинг на современные границы, gazetteer) и геокодинг — отдельные
подзадачи (упомянуто в ROADMAP §5.1.7, реализация позже).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from gedcom_parser.names import VariantKind

if TYPE_CHECKING:
    from gedcom_parser.models import GedcomRecord


# -----------------------------------------------------------------------------
# Координаты
# -----------------------------------------------------------------------------

CoordinateKind = Literal["lat", "long"]

# Допускаем «N51.5074», «N 51.5074», «51.5074», «-0.1278». Регистр N/S/E/W —
# любой; пробелы между префиксом и числом — терпим.
_COORD_RE: re.Pattern[str] = re.compile(
    r"^\s*(?P<sign>[NSEWnsew])?\s*(?P<num>[+-]?\d+(?:\.\d+)?)\s*$"
)


def parse_coordinate(value: str | None, kind: CoordinateKind) -> float | None:
    """Распарсить значение ``LATI``/``LONG`` в decimal degrees.

    Поддерживаемые формы:

    * ``"N51.5074"`` — GEDCOM standard (S/W → отрицательное).
    * ``"51.5074"`` — без префикса (как есть, со знаком из числа).
    * ``"-0.1278"`` — отрицательное число.
    * ``"+34.05"`` — явный положительный знак.

    Args:
        value: Сырая строка значения.
        kind: ``"lat"`` для широты (N/S), ``"long"`` для долготы (E/W).

    Returns:
        Координата в градусах (с учётом полушария), либо ``None``,
        если строка пуста, не распарсилась, или префикс не подходит для
        ``kind`` (например, ``"E51"`` для широты).
    """
    if not value:
        return None
    m = _COORD_RE.match(value)
    if m is None:
        return None
    sign_letter = m.group("sign")
    num_str = m.group("num")

    try:
        num = float(num_str)
    except ValueError:
        return None

    if sign_letter is None:
        return num

    sign_letter = sign_letter.upper()
    if kind == "lat":
        if sign_letter not in ("N", "S"):
            return None
        return -num if sign_letter == "S" else num
    # kind == "long"
    if sign_letter not in ("E", "W"):
        return None
    return -num if sign_letter == "W" else num


# -----------------------------------------------------------------------------
# Иерархия
# -----------------------------------------------------------------------------


def parse_place_levels(raw: str | None) -> tuple[str, ...]:
    """Расщепить значение PLAC по запятым на уровни иерархии.

    Пустые сегменты (``"Slonim,, Russian Empire"``) выбрасываются.
    Окружающие пробелы каждой части срезаются. Пустой/``None`` ввод
    возвращает ``()``.

    Порядок сохраняется как в файле: от самого узкого (село/город)
    к самому широкому (страна/империя).
    """
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


# -----------------------------------------------------------------------------
# Варианты (FONE / ROMN) — повторяем семантику NameVariant
# -----------------------------------------------------------------------------


class PlaceVariant(BaseModel):
    """Альтернативная запись названия места — фонетическая или романизированная.

    Соответствует подтегам ``2 FONE`` / ``2 ROMN`` под ``1 PLAC`` в
    GEDCOM 5.5.1+ §3.5. Структура и семантика аналогичны
    :class:`gedcom_parser.names.NameVariant`.
    """

    value: str = Field(description="Сырое значение варианта.")
    kind: VariantKind
    type_: str | None = Field(
        default=None,
        description="Подтег TYPE (например, polish/yiddish/IPA).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


# -----------------------------------------------------------------------------
# Главная модель
# -----------------------------------------------------------------------------


class ParsedPlace(BaseModel):
    """Нормализованное место.

    Поля:

    * ``raw`` — оригинальное значение PLAC (round-trip).
    * ``levels`` — кортеж уровней от узкого к широкому.
    * ``latitude`` / ``longitude`` — координаты в decimal degrees,
      южная широта и западная долгота отрицательные.
    * ``form`` — значение PLAC.FORM (шаблон уровней), если задано
      непосредственно на этом PLAC.
    * ``variants`` — кортеж :class:`PlaceVariant` из FONE/ROMN.

    Историческая нормализация (Wilno/Vilnius, исторические границы)
    отложена.
    """

    raw: str = Field(description="Оригинальное значение PLAC.")
    levels: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None
    form: str | None = Field(
        default=None,
        description="Подтег FORM (шаблон уровней, например 'City, County, Country').",
    )
    variants: tuple[PlaceVariant, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")

    @classmethod
    def from_record(cls, record: GedcomRecord) -> ParsedPlace:
        """Построить ``ParsedPlace`` из узла ``PLAC``.

        Args:
            record: Узел тега ``PLAC`` (с возможными ``MAP``, ``FORM``,
                ``FONE``, ``ROMN`` дочерними).
        """
        raw = record.value
        levels = parse_place_levels(raw)
        form = record.get_value("FORM") or None

        latitude: float | None = None
        longitude: float | None = None
        map_node = record.find("MAP")
        if map_node is not None:
            latitude = parse_coordinate(map_node.get_value("LATI"), "lat")
            longitude = parse_coordinate(map_node.get_value("LONG"), "long")

        variants = _collect_place_variants(record)

        return cls(
            raw=raw,
            levels=levels,
            latitude=latitude,
            longitude=longitude,
            form=form,
            variants=variants,
        )


def _collect_place_variants(record: GedcomRecord) -> tuple[PlaceVariant, ...]:
    """Собрать FONE / ROMN-подтеги в кортеж :class:`PlaceVariant`."""
    out: list[PlaceVariant] = []
    for child in record.children:
        if child.tag == "FONE":
            out.append(
                PlaceVariant(
                    value=child.value,
                    kind="phonetic",
                    type_=child.get_value("TYPE") or None,
                )
            )
        elif child.tag == "ROMN":
            out.append(
                PlaceVariant(
                    value=child.value,
                    kind="romanized",
                    type_=child.get_value("TYPE") or None,
                )
            )
    return tuple(out)


__all__ = [
    "CoordinateKind",
    "ParsedPlace",
    "PlaceVariant",
    "parse_coordinate",
    "parse_place_levels",
]
