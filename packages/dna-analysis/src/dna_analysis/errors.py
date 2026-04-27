"""Исключения парсеров DNA.

Сообщения исключений НИКОГДА не должны содержать raw value SNP
(rsid, position, genotype). Только line number, тип ошибки, имя поля.
См. ADR-0012 §«Privacy guards в коде».
"""

from __future__ import annotations


class DnaError(Exception):
    """Базовый класс для всех ошибок dna-analysis."""


class DnaParseError(DnaError):
    """Ошибка парсинга DNA-файла.

    Атрибуты:
        line_number: Номер строки в исходном файле (1-based) или None
            для ошибок уровня файла (заголовок, encoding).
        reason: Краткое описание проблемы. БЕЗ raw value — только тип
            ошибки и имя поля (например "invalid chromosome" вместо
            "invalid chromosome '99'").
    """

    def __init__(self, reason: str, line_number: int | None = None) -> None:
        self.reason = reason
        self.line_number = line_number
        prefix = f"line {line_number}: " if line_number is not None else ""
        super().__init__(f"{prefix}{reason}")


class UnsupportedFormatError(DnaError):
    """Парсер не распознал формат файла или формат пока не реализован.

    Используется как для detection-failure, так и для stub-парсеров
    (MyHeritage / FTDNA в Phase 6.0).
    """
