"""Абстрактный базовый класс для DNA-парсеров.

Контракт: парсер — pure function от строкового содержимого файла к
DnaTest. Никаких side effects (файловые операции, сеть, логирование
raw values). Логировать можно только агрегаты (см. ADR-0012).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from dna_analysis.models import DnaTest


class BaseDnaParser(ABC):
    """Базовый интерфейс DNA-парсера raw-файлов."""

    @classmethod
    @abstractmethod
    def detect(cls, content: str) -> bool:
        """Возвращает True, если парсер распознаёт формат content.

        Должен смотреть только на первые ~20 строк (header сигнатуры
        провайдера). Не парсит весь файл, чтобы быть дешёвой
        преddетекшен-операцией для file-format dispatch.
        """

    @abstractmethod
    def parse(self, content: str) -> DnaTest:
        """Парсит raw-файл в DnaTest.

        Raises:
            DnaParseError: При невалидной строке (chromosome / genotype /
                position не проходят валидацию). Сообщение содержит line
                number и тип ошибки, но НЕ raw value (см. ADR-0012).
            UnsupportedFormatError: Если формат не распознан (вызовите
                detect() заранее, чтобы избежать).
        """
