"""Pydantic-модели публичного API ai-layer.

Эти модели — контракт между LLM (через structured-output) и остальной
системой. Изменение их полей — breaking change для downstream-сервисов
(parser-service, inference-service Phase 10.1+), поэтому версионирование
прошивается в имени модели (``HypothesisSuggestion`` → ``HypothesisSuggestionV2``
в будущем) и/или в имени prompt-шаблона.

См. ADR-0043 §«Prompt versioning» и ADR-0057.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

EvidenceDirectionLabel = Literal["supports", "contradicts", "neutral"]
ConfidenceLabel = Literal["low", "medium", "high"]
LocaleLabel = Literal["en", "ru"]


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


# -----------------------------------------------------------------------------
# Phase 10.1 — hypothesis explanation (см. ADR-0057).
# -----------------------------------------------------------------------------


class PersonSubject(BaseModel):
    """Описание одной персоны в паре сравнения для use-case'а explain_hypothesis.

    Attributes:
        id: Stable opaque-идентификатор (например, ``"person:42"``);
            используется в промпте, чтобы LLM мог различать subjects A/B.
            Не обязательно UUID — caller-уровень волен передавать любую
            непустую строку.
        summary: Свободно-форматное описание персоны (имя, даты, места)
            на естественном языке. Это **денормализованный snapshot** —
            мы не вытягиваем поля по отдельности, чтобы не хардкодить
            модель Person в контракт ai-layer (см. ADR-0057 §«Decoupling
            from shared-models»).
    """

    id: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class EvidenceItem(BaseModel):
    """Один атомарный пункт evidence из inference-engine для LLM-объяснения.

    В отличие от ``inference_engine.types.Evidence`` мы НЕ зависим от
    Pydantic-модели inference-engine: caller-уровень мапит поля сам,
    мы получаем уже сериализованную форму. Это даёт ai-layer
    независимость от inference-engine import-graph (см. ADR-0057).

    Attributes:
        rule_id: Идентификатор rule'а / источника evidence — для аудита.
        confidence: Уверенность в [0, 1] (доменная семантика — на стороне
            caller'а; обычно weight × prior, см. inference-engine composer).
        direction: ``supports`` / ``contradicts`` / ``neutral`` — куда
            evidence толкает гипотезу.
        details: Естественно-языковое описание evidence для LLM
            («Birth year exact match (1872)», «DNA share 1450 cM», …).
            Промпт-template требует, чтобы это была одна строка без
            переноса.
    """

    rule_id: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    direction: EvidenceDirectionLabel
    details: str = Field(min_length=1)


class HypothesisInput(BaseModel):
    """Вход для use-case'а ``explain_hypothesis``.

    Attributes:
        subjects: Ровно две персоны, между которыми считаем гипотезу
            ``same_person``. Tuple-форма даёт компилятор-уровневую
            гарантию arity=2 (нет «одного subject'а» / «трёх»).
        evidence: Плоский список evidence-items. Порядок не важен — LLM
            сам выбирает top-3 по силе. Может быть пустым (тогда LLM
            вернёт low-confidence объяснение «недостаточно данных»).
        composite_score: Опциональный заранее посчитанный composite
            score из inference-engine; передаётся LLM как hint, не как
            конечная истина.
    """

    subjects: Annotated[tuple[PersonSubject, PersonSubject], Field(min_length=2, max_length=2)]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    composite_score: float | None = Field(default=None, ge=0.0, le=1.0)


class HypothesisExplanationPayload(BaseModel):
    """Содержимое JSON-ответа LLM (без telemetry-полей).

    Структура зеркалит ``hypothesis_explanation_v1.md`` schema. Mы
    разделяем «что вернул LLM» (этот класс) и «что отдаём caller'у»
    (``HypothesisExplanation``, который добавляет tokens / cost) —
    парсинг и обогащение разнесены, тестировать проще.
    """

    summary: str = Field(min_length=1)
    key_evidence: list[str] = Field(default_factory=list, max_length=3)
    caveats: list[str] = Field(default_factory=list)
    confidence_label: ConfidenceLabel


class HypothesisExplanation(BaseModel):
    """Финальный результат ``explain_hypothesis``.

    Attributes:
        summary: 1–2 предложения, ground-truth-cited explanation.
        key_evidence: До трёх паунктов, отсортированных от сильнейшего.
        caveats: Что НЕ совпадает / вызывает сомнение. Может быть пустым,
            но обычно LLM находит хотя бы одно «no contradictions in supplied
            evidence».
        confidence_label: ``low`` / ``medium`` / ``high`` — нормализованный
            label, чтобы UI Phase 4.9 не интерпретировал произвольный
            float'ом.
        locale: Локаль ответа (``en``/``ru``) — для отладки и кеширования.
        tokens_used: Сумма input + output токенов (для биллинг-аудита).
        cost_usd: Расчётная стоимость вызова в USD по pricing-таблице
            ``ai_layer.pricing``.
        model: Имя модели, которая обслужила вызов (для аудита и
            при необходимости — повторного запроса).
        dry_run: ``True`` если ответ — mock из dry-run mode (без реального
            вызова Anthropic). Caller может фильтровать такие записи
            из аналитики.
    """

    summary: str = Field(min_length=1)
    key_evidence: list[str] = Field(default_factory=list, max_length=3)
    caveats: list[str] = Field(default_factory=list)
    confidence_label: ConfidenceLabel
    locale: LocaleLabel
    tokens_used: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    model: str
    dry_run: bool = False
