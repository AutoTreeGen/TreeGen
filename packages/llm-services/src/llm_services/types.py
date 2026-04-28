"""Pydantic-модели результатов LLM-операций.

Все типы — frozen Pydantic v2; LLM-вызов возвращает structured output
(`output_config={"format": {"type": "json_schema", ...}}`), и Pydantic
валидирует ответ перед возвратом наверх. Это перекрывает класс ошибок
«LLM вернул JSON с лишним ключом / пропущенным полем» — мы не молча
«додумываем» данные.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NormalizedPlace(BaseModel):
    """Канонизированное место (результат normalize_place_name).

    Поля:
        name: Каноническое название (например, ``"Slonim"``).
        country_code: ISO 3166-1 alpha-2 (``"BY"`` для современной Беларуси).
            Может быть ``None``, если место не сопоставимо с современной
            страной (например, «Дикое Поле» XVI века).
        historical_period: Свободно-форматная строка с историческим
            контекстом — «Russian Empire (1795–1917)» или «Polish-Lithuanian
            Commonwealth (1569–1795)». Не предназначена для машинного
            парсинга, только для UI explainability.
        confidence: Уверенность LLM в нормализации, ``[0.0, 1.0]``.
            Композеру inference-engine стоит использовать как множитель
            к weight'у Evidence (см. ADR-0030 §«Score interpretation»).
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    historical_period: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class NameCluster(BaseModel):
    """Кластер имён-вариантов одного человека (результат disambiguate_name_variants).

    Поля:
        canonical: Каноническая форма (обычно полное имя в latin-транслитерации).
        variants: Все варианты, попавшие в этот кластер, включая `canonical`.
            Порядок — как в исходном списке (стабильность для UI).
        confidence: Уверенность LLM, что варианты — действительно один человек.
            ``[0.0, 1.0]``.
    """

    model_config = ConfigDict(frozen=True)

    canonical: str = Field(min_length=1)
    variants: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


__all__ = ["NameCluster", "NormalizedPlace"]
