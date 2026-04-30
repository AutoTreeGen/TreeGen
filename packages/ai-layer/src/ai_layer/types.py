"""Pydantic-модели публичного API ai-layer.

Эти модели — контракт между LLM (через structured-output) и остальной
системой. Изменение их полей — breaking change для downstream-сервисов
(parser-service, inference-service Phase 10.1+), поэтому версионирование
прошивается в имени модели (``HypothesisSuggestion`` → ``HypothesisSuggestionV2``
в будущем) и/или в имени prompt-шаблона.

См. ADR-0043 §«Prompt versioning».
"""

from __future__ import annotations

from typing import Literal

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


# -----------------------------------------------------------------------------
# Phase 10.2 — source extraction (см. ADR-0059).
# -----------------------------------------------------------------------------


class PersonExtract(BaseModel):
    """Извлечённая Claude'ом персона из источника.

    Attributes:
        full_name: Имя как написано в источнике (raw, до нормализации).
        given_name: Имя/отчество отдельно, если LLM смог разделить.
        surname: Фамилия отдельно (часто в Восточной Европе фамилия
            писалась через прозвище / патроним — оставляем как есть,
            нормализация ниже по pipeline).
        sex: GEDCOM-style ``M``/``F``/``U`` или ``None`` если не указано.
        birth_date_raw: Сырая дата рождения как в источнике («1850»,
            «around 1840», «AT 1855»). LLM не нормализует — это работа
            ``gedcom_parser.parse_gedcom_date``.
        birth_place_raw: Сырое место рождения.
        death_date_raw: Сырая дата смерти.
        death_place_raw: Сырое место смерти.
        relationship_hints: Свободные подсказки о родственных связях
            этой персоны с другими в этом же документе («son of John»,
            «married to Anna»). Caller использует для построения
            relationship-rows на этапе review.
        raw_quote: Прямая цитата из источника, на которую LLM опирается.
            Hard-rule: должна присутствовать в исходном тексте дословно.
        confidence: Self-assessed уверенность LLM в правильности
            extraction'а ``[0, 1]``. Композитный score для UI вычисляется
            не здесь — учитывается document quality, source authority и т.п.
    """

    full_name: str = Field(min_length=1, max_length=512)
    given_name: str | None = Field(default=None, max_length=256)
    surname: str | None = Field(default=None, max_length=256)
    sex: Literal["M", "F", "U"] | None = None
    birth_date_raw: str | None = Field(default=None, max_length=128)
    birth_place_raw: str | None = Field(default=None, max_length=512)
    death_date_raw: str | None = Field(default=None, max_length=128)
    death_place_raw: str | None = Field(default=None, max_length=512)
    relationship_hints: list[str] = Field(default_factory=list)
    raw_quote: str = Field(min_length=1, max_length=2048)
    confidence: float = Field(ge=0.0, le=1.0)


class EventExtract(BaseModel):
    """Извлечённое событие (рождение, брак, перепись, эмиграция и т. п.).

    Attributes:
        event_type: GEDCOM-tag из ``shared_models.enums.EventType``,
            или ``CUSTOM`` если не классифицируется. Caller валидирует
            принадлежность к допустимому набору.
        date_raw: Сырая дата.
        place_raw: Сырое место.
        participants_hints: Имена участников (как в источнике); caller
            маппит на extracted persons по совпадению ``full_name``.
        description: Свободно-форменное описание события на английском,
            если есть нюансы (роль, обстоятельства).
        raw_quote: Прямая цитата из источника.
        confidence: ``[0, 1]``.
    """

    event_type: str = Field(min_length=1, max_length=32)
    date_raw: str | None = Field(default=None, max_length=128)
    place_raw: str | None = Field(default=None, max_length=512)
    participants_hints: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=1024)
    raw_quote: str = Field(min_length=1, max_length=2048)
    confidence: float = Field(ge=0.0, le=1.0)


class RelationshipExtract(BaseModel):
    """Извлечённая родственная связь между двумя именами в источнике.

    Attributes:
        person_a_name: Имя первого участника, как написано в источнике.
        person_b_name: Имя второго участника.
        relation_kind: ``parent``, ``child``, ``spouse``, ``sibling`` или
            ``other``. Свободно-форменный, валидируется caller'ом.
        raw_quote: Прямая цитата.
        confidence: ``[0, 1]``.
    """

    person_a_name: str = Field(min_length=1, max_length=512)
    person_b_name: str = Field(min_length=1, max_length=512)
    relation_kind: Literal["parent", "child", "spouse", "sibling", "other"]
    raw_quote: str = Field(min_length=1, max_length=2048)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    """Результат одного source-extraction вызова Claude.

    Attributes:
        persons: Извлечённые персоны (порядок сохранения = порядок
            упоминания в источнике).
        events: Извлечённые события.
        relationships: Извлечённые связи (соответствуют именам из
            ``persons[*].full_name``).
        document_summary: Короткое (1–3 предложения) резюме того, что
            это за документ — для UI «AI прочитал ваш источник как X».
        overall_confidence: Aggregate confidence: насколько хорошо LLM
            понял документ в целом. Если сильно ниже 0.5 — UI должен
            показать предупреждение «возможно низкое качество скана».
        language_detected: BCP-47 код языка документа (``ru``, ``pl``,
            ``he``, ``yi``, ``en``...) или ``"mixed"`` для multi-language.
    """

    persons: list[PersonExtract] = Field(default_factory=list)
    events: list[EventExtract] = Field(default_factory=list)
    relationships: list[RelationshipExtract] = Field(default_factory=list)
    document_summary: str = Field(min_length=1, max_length=2048)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    language_detected: str = Field(min_length=2, max_length=16)
