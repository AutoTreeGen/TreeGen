"""Иерархия исключений парсера GEDCOM.

Все ошибки наследуются от ``GedcomError``. Где возможно, в исключение записывается
номер строки (``line_no``) и фрагмент исходника (``snippet``) для диагностики.
"""

from __future__ import annotations


class GedcomError(Exception):
    """Базовое исключение для всех ошибок парсера GEDCOM."""

    def __init__(
        self,
        message: str,
        *,
        line_no: int | None = None,
        snippet: str | None = None,
    ) -> None:
        # Формируем читаемое сообщение с указанием места ошибки.
        parts = [message]
        if line_no is not None:
            parts.append(f"(line {line_no})")
        if snippet is not None:
            parts.append(f"-> {snippet!r}")
        super().__init__(" ".join(parts))
        self.message = message
        self.line_no = line_no
        self.snippet = snippet


class GedcomEncodingError(GedcomError):
    """Ошибка определения или декодирования кодировки GEDCOM-файла.

    Возникает, когда:
    - заявленная в HEAD CHAR кодировка неизвестна,
    - байты не декодируются ни одной из поддерживаемых кодировок,
    - реализация конкретной кодировки ещё не готова (например, ANSEL).
    """


class GedcomLexerError(GedcomError):
    """Синтаксическая ошибка на уровне отдельной строки GEDCOM.

    Возникает, когда строка не соответствует базовому формату
    ``LEVEL [XREF] TAG [VALUE]`` (см. GEDCOM 5.5.5, Chapter 1).
    """


class GedcomParseError(GedcomError):
    """Ошибка построения дерева записей из последовательности строк.

    Примеры: «прыжок» уровня (с 0 сразу на 2), CONT/CONC без родителя,
    некорректный xref на уровне 0.
    """


class GedcomDateParseError(GedcomError):
    """Ошибка разбора значения тега DATE.

    Поднимается :func:`gedcom_parser.dates.parse_gedcom_date`, когда строка не
    соответствует грамматике GEDCOM 5.5.5 §3.6 и не восстанавливается
    эвристиками. На уровне семантического слоя (``Event``, ``Header``)
    эта ошибка перехватывается, эмитируется :class:`GedcomDateWarning`,
    а ``date`` остаётся ``None`` (``date_raw`` сохраняется без потерь).
    """


# -----------------------------------------------------------------------------
# Предупреждения (warnings) парсера
# -----------------------------------------------------------------------------


class GedcomWarning(UserWarning):
    """Базовый класс для всех warning-ов парсера GEDCOM.

    Тесты могут целенаправленно игнорировать или ловить потомков этого класса:
    `pyproject.toml` содержит ``ignore::gedcom_parser.exceptions.GedcomWarning``,
    чтобы warning-ы на грязные реальные файлы не превращались в ошибки при
    общем правиле ``filterwarnings = ["error"]``.
    """


class GedcomEncodingWarning(GedcomWarning):
    """Предупреждение о компромиссе при декодировании.

    Например: ANSEL-кодировка детектится, но полноценного декодера ещё нет,
    поэтому используется fallback на latin1 с потерей не-ASCII символов.
    """


class GedcomLenientWarning(GedcomWarning):
    """Предупреждение о мягкой обработке невалидной строки.

    Например: строка не соответствует формату ``LEVEL [XREF] TAG [VALUE]``,
    но мы решили считать её продолжением значения предыдущей строки
    (характерное поведение экспорта MyHeritage и старых версий Geni).
    """


class GedcomReferenceWarning(GedcomWarning):
    """Предупреждение о висячей xref-ссылке в семантическом слое.

    Возникает при ``GedcomDocument.verify_references()`` или явном вызове
    резолвера, когда ``@X@`` указывает на несуществующую запись (типичные
    причины: обрезанный экспорт, подмена id'шек при слиянии деревьев,
    битые рукописные правки .ged).
    """


class GedcomDateWarning(GedcomWarning):
    """Предупреждение о невозможности нормализовать дату.

    Семантический слой (``Event``, ``Header``) при сборке вызывает
    :func:`gedcom_parser.dates.parse_gedcom_date` для каждого ``DATE``-значения.
    Если строка не парсится — поле ``date`` остаётся ``None``, ``date_raw``
    сохраняется как есть, а это предупреждение позволяет вызывающему коду
    собирать статистику битых дат без падения парсинга.
    """
