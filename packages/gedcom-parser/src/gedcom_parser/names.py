"""Нормализация имён (ROADMAP §5.1.6).

Расширяет базовое расщепление ``/Surname/``-нотации (см.
:func:`gedcom_parser.entities._split_name_value`) тремя приёмами:

1. **Патронимы.** Русско-белорусско-украинская традиция отчества по
   суффиксу (``-ович``/``-евич``/``-овна``/``-евна``/``-ична``/``-инична``).
   Эвристика «угадывает» патронимическое отчество в строке ``given`` и
   возвращает его отдельным полем; ``given`` сужается до личного имени.
   Для случаев, где автор GEDCOM-файла записал отчество как часть
   ``GIVN``-подтега (типичная практика BK6/MyHeritage RU-локали).

2. **Составные фамилии.** Двойные/тройные фамилии вида ``Petrov-Sidorov``
   разрезаются по дефису в кортеж ``surnames``. Базовое поле ``surname``
   сохраняет исходную строку (round-trip + сортировка).

3. **FONE / ROMN-варианты.** Подтеги ``2 FONE …`` и ``2 ROMN …`` под
   ``1 NAME …`` (GEDCOM 5.5.1+ §3.5) сохраняются как :class:`NameVariant`
   с явным ``kind`` (``"phonetic"`` или ``"romanized"``). Это база для
   систематической транслитерации в подпункте 8.

Hebrew/Yiddish особенности — отдельные :class:`gedcom_parser.entities.Name`
записи с ``type_`` (см. GEDCOM ``2 TYPE birth|married|aka|…``); сейчас
никаких эвристик специально под ``ben``/``bat`` не делаем — обычно такие
имена в GEDCOM приходят уже разделёнными подтегами или отдельным NAME.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# -----------------------------------------------------------------------------
# Патронимы
# -----------------------------------------------------------------------------

# Суффиксы русско-белорусско-украинских патронимов. Порядок важен:
# более длинные суффиксы должны проверяться первыми, чтобы "инична"
# (Ильинична) не съел префиксом "ична".
_PATRONYMIC_SUFFIXES: tuple[str, ...] = (
    "инична",  # Ильинична
    "ович",  # Иванович
    "евич",  # Сергеевич
    "овна",  # Ивановна
    "евна",  # Сергеевна
    "ична",  # Никитична
)

# Минимальная длина основы (часть слова до суффикса). Защищает от ложных
# срабатываний на коротких словах вроде "Ович" самих по себе.
_PATRONYMIC_MIN_STEM: int = 2


def detect_patronymic(given: str | None) -> tuple[str | None, str | None]:
    """Выделить отчество из строки ``given``.

    Эвристика: бьём строку на пробельные токены, для каждого проверяем
    окончание из :data:`_PATRONYMIC_SUFFIXES` (case-insensitive). Первый
    подходящий токен считается отчеством, остальные склеиваются обратно
    через пробел и возвращаются как новый ``given``.

    Args:
        given: Строка вида ``"Иван Иванович"``, ``"Мария Петровна"``,
            ``"John"`` (тогда отчество не найдётся), ``None``.

    Returns:
        Кортеж ``(новый_given, патронимик)``. Если отчество не найдено —
        ``(given_как_есть, None)``. Если в ``given`` остался единственный
        токен-патронимик и больше ничего — ``(None, патронимик)``.
    """
    if not given:
        return given, None

    tokens = given.split()
    for i, tok in enumerate(tokens):
        lower = tok.lower()
        for suf in _PATRONYMIC_SUFFIXES:
            if lower.endswith(suf) and len(tok) - len(suf) >= _PATRONYMIC_MIN_STEM:
                rest_tokens = tokens[:i] + tokens[i + 1 :]
                rest = " ".join(rest_tokens) if rest_tokens else None
                return rest, tok
    return given, None


# -----------------------------------------------------------------------------
# Составные фамилии
# -----------------------------------------------------------------------------


def split_compound_surname(surname: str | None) -> tuple[str, ...]:
    """Разрезать составную фамилию по дефису.

    ``"Petrov-Sidorov"``       → ``("Petrov", "Sidorov")``
    ``"Иванов-Петров-Сидоров"`` → ``("Иванов", "Петров", "Сидоров")``
    ``"Smith"``                → ``("Smith",)``
    ``""`` / ``None``          → ``()``

    Все части очищаются от окружающих пробелов; пустые сегменты
    (``"Smith--Jones"``) выбрасываются.
    """
    if not surname:
        return ()
    parts = [p.strip() for p in surname.split("-") if p.strip()]
    return tuple(parts)


# -----------------------------------------------------------------------------
# Phonetic / Romanized варианты (FONE / ROMN)
# -----------------------------------------------------------------------------


VariantKind = Literal["phonetic", "romanized"]


class NameVariant(BaseModel):
    """Альтернативная запись имени — фонетическая или романизированная.

    Соответствует подтегам ``2 FONE`` / ``2 ROMN`` под ``1 NAME`` в
    GEDCOM 5.5.1+ §3.5. ``type_`` повторяет содержимое подтега
    ``3 TYPE`` (например, ``"hebrew"``, ``"polish"``, ``"YIVO"``); если
    подтег не указан — ``None``.

    Сама строка-вариант хранится в ``value``. ``kind`` отличает
    транслитерацию (latin-letters proxy для не-latin оригинала) от
    фонетической записи (приближённое произношение).
    """

    value: str = Field(description="Сама строка варианта (как в файле).")
    kind: VariantKind
    type_: str | None = Field(
        default=None,
        description="Подтег TYPE (например, hebrew/polish/YIVO/IPA).",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = [
    "NameVariant",
    "VariantKind",
    "detect_patronymic",
    "split_compound_surname",
]
