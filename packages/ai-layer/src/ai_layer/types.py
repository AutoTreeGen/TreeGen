"""Pydantic-модели публичного API ai-layer.

Эти модели — контракт между LLM (через structured-output) и остальной
системой. Изменение их полей — breaking change для downstream-сервисов
(parser-service, inference-service Phase 10.1+), поэтому версионирование
прошивается в имени модели (``HypothesisSuggestion`` → ``HypothesisSuggestionV2``
в будущем) и/или в имени prompt-шаблона.

См. ADR-0043 §«Prompt versioning».
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HypothesisSuggestion(BaseModel):
    """LLM-предложение гипотезы для двух персон / одного факта.

    Совместимо с inference-engine ``Evidence``:
        rationale -> Evidence.note
        confidence -> Evidence.confidence
        evidence_refs -> Evidence.provenance.refs

    Attributes:
        rationale: Естественно-языковое объяснение гипотезы. Должно
            ссылаться на конкретные факты из ``evidence_refs``, не
            фантазировать новые. Validation на уровне use-case'а
            проверяет, что все ID из ``evidence_refs`` присутствуют
            во входных данных.
        confidence: Самооценка LLM в диапазоне ``[0, 1]``. Финальный
            ``composite_score`` гипотезы вычисляется не здесь — через
            inference-engine composer с учётом priors и других rules.
        evidence_refs: Идентификаторы фактов / источников, на которые
            опирается ``rationale``. Формат — opaque строки, выдаваемые
            caller'ом (parser-service передаст ``person_id``,
            ``source_id`` и т.п.).
    """

    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)


class EmbeddingResult(BaseModel):
    """Результат батч-вызова Voyage AI.

    Vектора возвращаются в том же порядке, что входные тексты; дубликаты
    в input'е (после нормализации) ссылаются на один и тот же индекс
    в ``vectors`` через ``index_map``.

    Attributes:
        vectors: Уникальные эмбеддинг-вектора (по одной строке на уникальный
            input). Длина одного вектора — фиксирована model_version
            (Voyage-3 → 1024).
        index_map: ``len(input_texts)``-длинный список индексов в ``vectors``.
            Позволяет восстановить порядок для caller'а без дублирования
            данных. Пример: input ``["a", "b", "a"]`` → vectors=2 элемента,
            index_map=``[0, 1, 0]``.
        model_version: Имя модели, которой получены вектора (для cache-инвалидации
            и аудита).
    """

    vectors: list[list[float]]
    index_map: list[int]
    model_version: str
