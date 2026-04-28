"""Pydantic-схемы для API parser-service.

DTOs из ``shared-models.schemas`` переиспользуем напрямую для read-моделей.
Здесь только response/request-schemas специфичные для HTTP-слоя.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from shared_models.schemas import ImportJobProgress


class ImportJobResponse(BaseModel):
    """Ответ на ``POST /imports``, ``GET /imports/{id}``, ``PATCH /cancel``.

    ``progress`` (Phase 3.5) — последний снапшот, опубликованный
    worker'ом в ``ImportJob.progress``. NULL пока worker не сделал
    первого ``ProgressPublisher.publish()``.

    ``cancel_requested`` — true после ``PATCH /imports/{id}/cancel``;
    переход status → ``cancelled`` делает worker между стадиями.

    ``events_url`` (Phase 3.5) — относительный путь до SSE-эндпоинта
    с live-стримом прогресса. UI подключается на 202 Accepted и
    держит соединение до терминальной стадии.
    """

    id: uuid.UUID
    tree_id: uuid.UUID
    status: str = Field(description="queued|running|succeeded|failed|partial|cancelled")
    source_filename: str | None = None
    source_sha256: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    progress: ImportJobProgress | None = None
    cancel_requested: bool = False
    events_url: str | None = Field(
        default=None,
        description="Относительный URL SSE-эндпоинта (только в ответе POST/PATCH).",
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PersonSummary(BaseModel):
    """Краткое представление персоны для списка."""

    id: uuid.UUID
    gedcom_xref: str | None = None
    sex: str
    confidence_score: float
    primary_name: str | None = Field(
        default=None,
        description="Первое имя из ``names`` (sort_order=0), если есть.",
    )
    match_type: Literal["substring", "phonetic"] | None = Field(
        default=None,
        description=(
            "Как этот ряд попал в результат: ``substring`` (ILIKE), "
            "``phonetic`` (Daitch-Mokotoff bucket overlap), либо ``None`` "
            "(простой list-эндпоинт без поиска). Phase 4.4.1."
        ),
    )

    model_config = ConfigDict(from_attributes=True)


class PersonListResponse(BaseModel):
    """Пагинированный список персон в дереве."""

    tree_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[PersonSummary]


class PlaceSummary(BaseModel):
    """Краткое представление места для встраивания в EventSummary."""

    id: uuid.UUID
    name: str = Field(
        validation_alias="canonical_name",
        description="Каноническое имя места (place.canonical_name).",
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class CitationSummary(BaseModel):
    """Краткая ссылка на источник для встраивания в EventSummary.

    `source_title` денормализован — берётся из join'а с `sources`,
    избавляет фронт от второго запроса.
    """

    source_id: uuid.UUID
    source_title: str
    page: str | None = None
    quality: float | None = None

    model_config = ConfigDict(from_attributes=True)


class EventSummary(BaseModel):
    """Событие персоны в карточке."""

    id: uuid.UUID
    event_type: str
    date_raw: str | None = None
    date_start: datetime | None = None
    date_end: datetime | None = None
    place_id: uuid.UUID | None = None
    place: PlaceSummary | None = None
    citations: list[CitationSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MultimediaSummary(BaseModel):
    """Краткое представление multimedia-объекта для PersonDetail.media[]."""

    id: uuid.UUID
    title: str | None = Field(
        default=None,
        validation_alias="caption",
        description="Caption медиа (MultimediaObject.caption).",
    )
    file_path: str = Field(
        validation_alias="storage_url",
        description="Путь/URL файла (MultimediaObject.storage_url).",
    )
    format: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class NameSummary(BaseModel):
    """Имя персоны в карточке."""

    id: uuid.UUID
    given_name: str | None = None
    surname: str | None = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class PersonDetail(BaseModel):
    """Детали персоны: персональные поля + связанные имена/события + media."""

    id: uuid.UUID
    tree_id: uuid.UUID
    gedcom_xref: str | None = None
    sex: str
    status: str
    confidence_score: float
    names: list[NameSummary]
    events: list[EventSummary]
    media: list[MultimediaSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AncestorTreeNode(BaseModel):
    """Узел pedigree-дерева для ``GET /persons/{id}/ancestors``.

    Рекурсивная структура: у каждой персоны опционально есть ``father``
    и ``mother`` — каждый сам же ``AncestorTreeNode``. Глубина рекурсии
    ограничена параметром ``generations`` запроса (см. trees.py).

    ``birth_year`` / ``death_year`` извлекаются из событий BIRT/DEAT
    через ``date_start.year`` (для read-only chart полной даты не нужно).
    """

    id: uuid.UUID
    primary_name: str | None = None
    birth_year: int | None = None
    death_year: int | None = None
    sex: str
    # Stub-поле для бейджа «DNA tested» в pedigree UI. На Phase 4.3 всегда False;
    # реальное значение придёт когда Phase 6 (DNA matching) свяжет персону с
    # подтверждённым DNA-китом. Поле уже здесь, чтобы фронтенд мог рендерить
    # бейдж без ожидания backend-изменений.
    dna_tested: bool = False
    father: AncestorTreeNode | None = None
    mother: AncestorTreeNode | None = None

    model_config = ConfigDict(from_attributes=True)


# Pydantic v2 рекурсивные модели — finalize forward references.
AncestorTreeNode.model_rebuild()


class AncestorsResponse(BaseModel):
    """Обёртка для ответа ``GET /persons/{id}/ancestors``.

    Помимо корневого узла отдаём ``generations_requested`` и
    ``generations_loaded`` — фронт показывает «загружено N из запрошенных M»,
    если родительских записей в дереве меньше глубины запроса.
    """

    person_id: uuid.UUID
    generations_requested: int
    generations_loaded: int
    root: AncestorTreeNode


# -----------------------------------------------------------------------------
# Phase 3.4 — entity resolution (dedup) suggestions.
# Алгоритмы — pure functions в ``packages/entity-resolution/``;
# сервисный слой возвращает только эти DTO. См. ADR-0015.
# -----------------------------------------------------------------------------

EntityType = Literal["source", "place", "person"]


class DuplicateSuggestion(BaseModel):
    """Пара кандидатов на дедупликацию с confidence score.

    Никаких side-effects: просто read-only payload. Решение о merge —
    через UI Phase 4.5 с manual approval (CLAUDE.md §5).

    `components` — покомпонентный breakdown скорера для explainability:
    UI показывает "совпали по DM-bucket + birth_year ±1".
    `evidence` — human-readable diff (canonical names, dates etc.),
    позволяет user'у принять решение без ещё одного round-trip.
    """

    entity_type: EntityType
    entity_a_id: uuid.UUID
    entity_b_id: uuid.UUID
    confidence: float = Field(ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class DuplicateSuggestionListResponse(BaseModel):
    """Пагинированный ответ ``GET /trees/{id}/duplicate-suggestions``."""

    tree_id: uuid.UUID
    entity_type: EntityType | None = None
    min_confidence: float
    total: int
    limit: int
    offset: int
    items: list[DuplicateSuggestion]


# ---------------------------------------------------------------------------
# Phase 3.6 — Source / Citation evidence read API.
# Поддерживает «откуда мы это знаем»-UI: отдельный source viewer (Phase 4.7)
# и citations-список на карточке персоны.
# ---------------------------------------------------------------------------


class SourceSummary(BaseModel):
    """Краткое представление SOUR-записи для списка `/trees/{id}/sources`.

    `citation_count` — сколько entity-ссылок на этот источник (persons +
    families + events). Денормализован одним LEFT JOIN COUNT в эндпоинте,
    чтобы UI не делал N round-trip'ов за деталями каждого Source.
    """

    id: uuid.UUID
    gedcom_xref: str | None = None
    title: str
    abbreviation: str | None = None
    author: str | None = None
    publication: str | None = None
    repository: str | None = None
    source_type: str
    citation_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class SourceListResponse(BaseModel):
    """Пагинированный ответ ``GET /trees/{id}/sources``."""

    tree_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[SourceSummary]


class SourceLinkedEntity(BaseModel):
    """Сущность, которая ссылается на источник через citation.

    `table` ∈ ``{"person", "family", "event"}`` (полиморфная связь
    `citations.entity_type` / `entity_id`). UI разрешает её в
    конкретный card view на стороне клиента.

    `display_label` (Phase 4.7-finalize) — denormalized human-readable
    label, чтобы UI не делал отдельный round-trip за именем каждого
    person'а: "John Smith" для person, "BIRT 1850" для event,
    "Smith × Cohen" для family. ``None`` если резолвер не нашёл
    подходящее имя (orphan FK, soft-deleted person и пр.).
    """

    table: Literal["person", "family", "event"]
    id: uuid.UUID
    page: str | None = None
    quay_raw: int | None = None
    quality: float
    display_label: str | None = None


class SourceDetail(BaseModel):
    """Детали SOUR-записи + список linked-сущностей.

    Полный набор полей, нормализованный в Phase 3.6: TITL / AUTH / PUBL /
    ABBR / TEXT / REPO. `linked` — все entity'ы которые цитируют этот
    источник (любая комбинация person / family / event).
    """

    id: uuid.UUID
    tree_id: uuid.UUID
    gedcom_xref: str | None = None
    title: str
    abbreviation: str | None = None
    author: str | None = None
    publication: str | None = None
    repository: str | None = None
    text_excerpt: str | None = None
    source_type: str
    linked: list[SourceLinkedEntity] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class PersonCitationDetail(BaseModel):
    """Один citation на странице `/persons/{id}/citations`.

    Чем-то отличается от `CitationSummary` (используется внутри
    EventSummary): денормализован источник целиком (title + abbreviation),
    плюс QUAY raw + derived confidence + EVEN/ROLE для evidence-graph
    рендера. `entity_type` и `entity_id` показывают, к какой сущности
    привязан citation: к самой персоне или к одному из её событий.
    """

    id: uuid.UUID
    source_id: uuid.UUID
    source_title: str
    source_abbreviation: str | None = None
    entity_type: Literal["person", "family", "event"]
    entity_id: uuid.UUID
    page: str | None = None
    quay_raw: int | None = None
    quality: float
    event_type: str | None = None
    role: str | None = None
    note: str | None = None
    quoted_text: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PersonCitationsResponse(BaseModel):
    """Ответ ``GET /persons/{id}/citations``."""

    person_id: uuid.UUID
    total: int
    items: list[PersonCitationDetail]


# ---------------------------------------------------------------------------
# FamilySearch import (Phase 5.1) — см. ADR-0017
# ---------------------------------------------------------------------------


class FamilySearchImportRequest(BaseModel):
    """Параметры ``POST /imports/familysearch``.

    ``access_token`` обрабатывается **stateless**: используется только для
    одного запроса в FamilySearch API и не сохраняется ни в БД, ни в
    логах. Для traceability логируется ``sha256(access_token)[:8]``.
    """

    access_token: str = Field(
        min_length=10,
        description="OAuth access token (получает caller через PKCE flow).",
    )
    fs_person_id: str = Field(
        pattern=r"^[A-Z0-9-]+$",
        max_length=64,
        description="FamilySearch person id (например, KW7S-VQJ).",
    )
    tree_id: uuid.UUID = Field(description="ID существующего дерева в AutoTreeGen.")
    generations: int = Field(
        default=4,
        ge=1,
        le=8,
        description=(
            "Глубина pedigree (FamilySearch personal apps cap = 8). "
            "1 — только родители, 8 — максимум."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class FamilySearchImportResponse(ImportJobResponse):
    """Ответ ``POST /imports/familysearch`` — ImportJobResponse + Phase 5.2.1 fields.

    Расширяет базовый ImportJobResponse параметром ``review_url``, ведущим
    на UI-страницу review FS-flagged dedup-attempts. Сама ``stats``
    остаётся ``dict[str, int]``: ``fs_dedup_attempts_created`` лежит
    внутри неё.
    """

    review_url: str | None = Field(
        default=None,
        description=(
            "Относительный URL UI-страницы review FS-flagged duplicates. "
            "None если не было создано ни одного attempt'а в этом импорте."
        ),
    )


# -----------------------------------------------------------------------------
# Phase 5.1 — server-side OAuth flow + preview + async import (ADR-0027).
# -----------------------------------------------------------------------------


class FamilySearchOAuthStartResponse(BaseModel):
    """Ответ ``GET /imports/familysearch/oauth/start``.

    UI получает ``authorize_url`` и редиректит браузер. ``state`` тоже
    приходит — он же положен в HttpOnly cookie callback'ом, но видеть
    его на стороне фронта (например, для логов) нормально, секрет в
    нём только в сочетании с ``code_verifier``.
    """

    authorize_url: str = Field(description="OAuth authorize URL FamilySearch с PKCE параметрами.")
    state: str = Field(description="CSRF-state. Возвращается callback'ом для матчинга.")
    expires_in: int = Field(
        description="Сколько секунд state валиден (TTL Redis-ключа).",
    )


class FamilySearchAccountInfo(BaseModel):
    """Ответ ``GET /imports/familysearch/me`` — текущий FS-аккаунт.

    Если user не подключал FS — поле ``connected=False`` и остальное None.
    Не пробрасываем ``access_token`` или ``refresh_token`` сюда никогда.
    """

    connected: bool = Field(description="True если у user'а есть валидный токен в БД.")
    fs_user_id: str | None = Field(default=None, description="FamilySearch person id.")
    scope: str | None = None
    expires_at: datetime | None = Field(
        default=None,
        description="Когда access_token протухнет (UTC). UI может показать «осталось N часов».",
    )
    needs_refresh: bool = Field(
        default=False,
        description="True если до expires_at < 60s — фронт пусть ретраит.",
    )


class FamilySearchPedigreePreviewPerson(BaseModel):
    """Sample-запись для preview: ровно столько, чтобы user узнал «своих»."""

    fs_person_id: str
    primary_name: str | None = None
    lifespan: str | None = Field(
        default=None,
        description="«b. 1850 – d. 1920», если есть Birth/Death даты.",
    )


class FamilySearchPedigreePreviewResponse(BaseModel):
    """Ответ ``GET /imports/familysearch/pedigree/preview``.

    Не создаёт ImportJob, не пишет ничего в БД. Просто читает FS API
    и отдаёт frontend'у summary, чтобы пользователь подтвердил.
    """

    fs_focus_person_id: str
    generations: int
    person_count: int = Field(
        ge=0,
        description="Сколько уникальных персон в pedigree (включая focus).",
    )
    sample_persons: list[FamilySearchPedigreePreviewPerson] = Field(
        default_factory=list,
        description="Первые N (по умолчанию 10) персон — для visual confirmation.",
    )
    fs_user_id: str | None = Field(
        default=None,
        description="С какого FS-аккаунта подтянули (для UI «logged in as ...»).",
    )


class FamilySearchAsyncImportRequest(BaseModel):
    """Тело ``POST /imports/familysearch/import``.

    Использует токен, сохранённый сервером (server-side OAuth). Если
    токена нет — endpoint вернёт 409 «not connected». Сам импорт
    выполняется в arq worker'е (см. ``worker.run_fs_import_job``).

    ``access_token`` сюда **не передаётся** — это отличает async-flow
    от legacy-stateless ``POST /imports/familysearch``.
    """

    fs_person_id: str = Field(
        pattern=r"^[A-Z0-9-]+$",
        max_length=64,
        description="FamilySearch person id (focus persona).",
    )
    tree_id: uuid.UUID = Field(description="ID существующего дерева в AutoTreeGen.")
    generations: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Глубина pedigree (FamilySearch personal apps cap = 8).",
    )

    model_config = ConfigDict(extra="forbid")


# -----------------------------------------------------------------------------
# Phase 5.2.1 — FS dedup attempts (review queue, see ADR Option C).
# -----------------------------------------------------------------------------


FsDedupAttemptStatus = Literal["pending", "rejected", "merged", "all"]


class FsDedupAttemptOut(BaseModel):
    """Одна запись из ``fs_dedup_attempts`` для review-UI.

    Status — производное от пары ``(rejected_at, merged_at)``:
    ``pending`` если оба None, ``rejected`` если задан ``rejected_at``,
    ``merged`` если задан ``merged_at``.
    """

    id: uuid.UUID
    tree_id: uuid.UUID
    fs_person_id: uuid.UUID
    candidate_person_id: uuid.UUID
    score: float
    reason: str | None = None
    fs_pid: str | None = None
    rejected_at: datetime | None = None
    merged_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    provenance: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "rejected", "merged"]

    model_config = ConfigDict(from_attributes=True)


class FsDedupAttemptListResponse(BaseModel):
    """Пагинированный ответ ``GET /trees/{tree_id}/dedup-attempts``."""

    tree_id: uuid.UUID
    status: FsDedupAttemptStatus
    total: int
    limit: int
    offset: int
    items: list[FsDedupAttemptOut]


class FsDedupAttemptRejectRequest(BaseModel):
    """Тело ``POST /dedup-attempts/{id}/reject``."""

    reason: str | None = Field(
        default=None,
        max_length=1000,
        description="Опциональный комментарий пользователя об отказе.",
    )

    model_config = ConfigDict(extra="forbid")


class FsDedupAttemptMergeRequest(BaseModel):
    """Тело ``POST /dedup-attempts/{id}/merge``.

    ``confirm`` обязателен и должен быть ``True`` (CLAUDE.md §5
    enforce'нут как код): без явного подтверждения backend отвечает 400.
    Сам merge — не здесь, а на Phase 4.6 ``POST /persons/{id}/merge``;
    этот endpoint только проставляет ``merged_at`` на attempt-row и
    отдаёт URL обработчика.
    """

    confirm: Literal[True]

    model_config = ConfigDict(extra="forbid")


class FsDedupAttemptMergeResponse(BaseModel):
    """Ответ ``POST /dedup-attempts/{id}/merge`` (без выполнения merge'а)."""

    attempt_id: uuid.UUID
    fs_person_id: uuid.UUID
    candidate_person_id: uuid.UUID
    merged_at: datetime
    merge_url: str = Field(
        description=(
            "Относительный URL Phase 4.6 merge-preview/commit endpoint'а. "
            "UI делает следующий шаг там; данный attempt уже помечен как merged."
        )
    )


# -----------------------------------------------------------------------------
# Phase 7.2 — hypothesis persistence (ADR-0021).
# Pydantic-обёртки вокруг ORM моделей Hypothesis / HypothesisEvidence
# (shared-models.orm.hypothesis). Здесь — read/write DTO для HTTP слоя.
# -----------------------------------------------------------------------------


class HypothesisEvidenceResponse(BaseModel):
    """Один evidence-row для UI explainability."""

    id: uuid.UUID
    rule_id: str
    direction: str  # "supports" | "contradicts" | "neutral"
    weight: float
    observation: str
    source_provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class HypothesisSummary(BaseModel):
    """Облегчённый Hypothesis для list-эндпоинтов (без evidences[])."""

    id: uuid.UUID
    tree_id: uuid.UUID
    hypothesis_type: str
    subject_a_type: str
    subject_a_id: uuid.UUID
    subject_b_type: str
    subject_b_id: uuid.UUID
    composite_score: float
    computed_at: datetime
    rules_version: str
    reviewed_status: str
    reviewed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class HypothesisResponse(HypothesisSummary):
    """Полный Hypothesis с evidences[] — для GET /hypotheses/{id} и POST."""

    review_note: str | None = None
    reviewed_by_user_id: uuid.UUID | None = None
    evidences: list[HypothesisEvidenceResponse] = Field(default_factory=list)


class HypothesisListResponse(BaseModel):
    """Пагинированный list для ``GET /trees/{id}/hypotheses``."""

    tree_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[HypothesisSummary]


class HypothesisCreateRequest(BaseModel):
    """``POST /trees/{tree_id}/hypotheses`` body."""

    subject_a_id: uuid.UUID
    subject_b_id: uuid.UUID
    hypothesis_type: Literal[
        "same_person",
        "parent_child",
        "siblings",
        "marriage",
        "duplicate_source",
        "duplicate_place",
    ]

    model_config = ConfigDict(extra="forbid")


class HypothesisReviewRequest(BaseModel):
    """``PATCH /hypotheses/{id}/review`` body — user judgment.

    CLAUDE.md §5: ``status='confirmed'`` НЕ автоматически мерджит entities.
    Сервис только сохраняет user-judgment + actor; merge — отдельный
    flow Phase 4.6.

    ``deferred`` (Phase 4.9): «вернусь позже» — UI прячет из default
    pending queue, но не блокирует merge как ``rejected``.
    """

    status: Literal["pending", "confirmed", "rejected", "deferred"]
    note: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(extra="forbid")


# -----------------------------------------------------------------------------
# Phase 7.5 — bulk hypothesis compute (extension of Phase 7.2).
# Эндпоинты, обёртывающие ``services/bulk_hypothesis_runner.py``: один
# job на всё дерево, batched processing, прогресс в jsonb, cancel-флаг.
# -----------------------------------------------------------------------------


class HypothesisComputeJobProgress(BaseModel):
    """Прогресс одного bulk-compute job'а.

    Зеркалирует ``HypothesisComputeJob.progress`` (jsonb): обновляется
    worker'ом между batch'ами. UI читает по polling'у GET-эндпоинта.
    """

    processed: int = Field(ge=0, description="Кол-во обработанных pair'ов.")
    total: int = Field(ge=0, description="Общее кол-во candidate pair'ов на старте.")
    hypotheses_created: int = Field(
        ge=0,
        description=(
            "Кол-во hypothesis row'ов, попавших в результат job'а "
            "(включая идемпотентные re-fetch'и существующих)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class HypothesisComputeJobResponse(BaseModel):
    """Ответ POST /compute-all и GET/PATCH compute-jobs эндпоинтов.

    Из ORM ``HypothesisComputeJob`` (см. shared_models.orm.hypothesis_compute_job).
    Используется как 200/201/202 body — конкретный код зависит от endpoint'а
    (см. router в ``api/hypotheses.py``).
    """

    id: uuid.UUID
    tree_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    rule_ids: list[str] | None = Field(
        default=None,
        description=(
            "Whitelist rule_id'ов, которые worker должен исполнять. "
            "Currently informational: сохраняется в job-row для audit, "
            "но worker использует default rules pack из hypothesis_runner. "
            "Полная фильтрация — отдельный follow-up PR (см. PR #87 TODO)."
        ),
    )
    progress: HypothesisComputeJobProgress
    cancel_requested: bool = Field(
        description=(
            "True если PATCH /cancel был вызван. Worker проверяет флаг между "
            "batch'ами и переводит status → cancelled."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Краткий текст ошибки (для FAILED). Полный traceback — в logs.",
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    events_url: str | None = Field(
        default=None,
        description=(
            "Относительный URL SSE-эндпоинта (Phase 7.5 finalize). "
            "Возвращается только в ответах POST/PATCH; для GET клиент "
            "уже знает url своего стрима."
        ),
    )

    model_config = ConfigDict(from_attributes=True)


class BulkComputeRequest(BaseModel):
    """Тело POST /trees/{id}/hypotheses/compute-all.

    ``rule_ids`` — опциональный whitelist. Сейчас informational
    (см. ``HypothesisComputeJobResponse.rule_ids``); сохраняется в job-row,
    но worker исполняет defaults. Тестам и UI важно, что поле принимается
    без 400 — это forward-compatible.
    """

    rule_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional whitelist rule_id'ов. None = use defaults. "
            "Currently informational (см. PR #87 TODO для full filter)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# -----------------------------------------------------------------------------
# Phase 4.6 — manual person merge (ADR-0022)
# -----------------------------------------------------------------------------


SurvivorChoice = Literal["left", "right"]
HypothesisCheckStatus = Literal[
    "no_hypotheses_found",
    "no_conflicts",
    "conflicts_blocking",
]


class MergeFieldDiff(BaseModel):
    """Изменение одного скалярного поля Person после merge'а."""

    field: str
    survivor_value: Any
    merged_value: Any
    after_merge_value: Any

    model_config = ConfigDict(extra="forbid")


class MergeNameDiff(BaseModel):
    """Имя merged'а с новым sort_order (offset +1000)."""

    name_id: uuid.UUID
    old_sort_order: int
    new_sort_order: int

    model_config = ConfigDict(extra="forbid")


class MergeEventDiff(BaseModel):
    """Что произойдёт с событием при merge'е."""

    event_id: uuid.UUID
    action: Literal["reparent", "collapse_into_survivor", "keep_separate"]
    collapsed_into: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class MergeFamilyMembershipDiff(BaseModel):
    """Family-FK переключается с merged на survivor."""

    table: Literal[
        "families.husband_id",
        "families.wife_id",
        "family_children.child_person_id",
    ]
    row_id: uuid.UUID

    model_config = ConfigDict(extra="forbid")


class MergeHypothesisConflict(BaseModel):
    """Одна блокирующая проблема Hypothesis-gate."""

    reason: Literal[
        "rejected_same_person",
        "subject_already_merged",
        "cross_relationship_conflict",
    ]
    hypothesis_id: uuid.UUID | None = None
    detail: str

    model_config = ConfigDict(extra="forbid")


class MergePreviewResponse(BaseModel):
    """Ответ ``POST /persons/{id}/merge/preview`` — диффы без mutation'а."""

    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    default_survivor_id: uuid.UUID = Field(
        description=(
            "Дефолтный выбор survivor'а по ADR-0022 §Survivor selection "
            "(provenance count → confidence → created_at). UI может "
            "переключить через survivor_choice в commit body."
        )
    )
    fields: list[MergeFieldDiff] = Field(default_factory=list)
    names: list[MergeNameDiff] = Field(default_factory=list)
    events: list[MergeEventDiff] = Field(default_factory=list)
    family_memberships: list[MergeFamilyMembershipDiff] = Field(default_factory=list)
    hypothesis_check: HypothesisCheckStatus = "no_hypotheses_found"
    conflicts: list[MergeHypothesisConflict] = Field(default_factory=list)


class MergeCommitRequest(BaseModel):
    """Тело ``POST /persons/{id}/merge``.

    `confirm` обязателен и должен быть ``True`` — без этого 400.
    `confirm_token` — клиентский UUID для идемпотентности повторного POST.
    """

    target_id: uuid.UUID
    confirm: Literal[True]
    confirm_token: str = Field(min_length=8, max_length=64)
    survivor_choice: SurvivorChoice | None = Field(
        default=None,
        description=(
            "Выбор UI: 'left' = текущая (path) персона survivor, 'right' = "
            "target_id survivor. Если None — берётся default_survivor_id."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class MergeCommitResponse(BaseModel):
    """Ответ ``POST /persons/{id}/merge`` после успешного коммита."""

    merge_id: uuid.UUID
    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    merged_at: datetime
    confirm_token: str


class MergeUndoResponse(BaseModel):
    """Ответ ``POST /persons/merge/{id}/undo``."""

    merge_id: uuid.UUID
    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    undone_at: datetime


class MergeHistoryItem(BaseModel):
    """Одна merge-запись в `GET /persons/{id}/merge-history`."""

    merge_id: uuid.UUID
    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    merged_at: datetime
    undone_at: datetime | None = None
    purged_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MergeHistoryResponse(BaseModel):
    """Список merge'ей где person участвовала (как survivor или merged)."""

    person_id: uuid.UUID
    items: list[MergeHistoryItem]


# =============================================================================
# Phase 11.0 — Tree sharing & invitations (см. ADR-0036).
# =============================================================================


class InvitationCreateRequest(BaseModel):
    """Тело ``POST /trees/{tree_id}/invitations``.

    ``role`` принимает ``editor`` или ``viewer``; OWNER нельзя пригласить —
    только transfer'нуть существующему membership'у через ``PATCH /memberships/{id}``.
    """

    email: str = Field(min_length=3, max_length=254)
    role: Literal["editor", "viewer"] = "viewer"


class InvitationResponse(BaseModel):
    """Read-модель приглашения. Возвращается owner'у дерева."""

    id: uuid.UUID
    tree_id: uuid.UUID
    invitee_email: str
    role: str
    token: uuid.UUID = Field(
        description=(
            "Секрет accept-link'а. Возвращается owner'у на create'е, "
            "и в `GET /trees/{id}/invitations` (owner-only). Не доступен "
            "publicly. invitee получает только URL — не саму строку токена "
            "в API-ответе."
        ),
    )
    invite_url: str = Field(
        description=(
            "Готовый shareable-link `${PUBLIC_BASE_URL}/invitations/{token}`. "
            "Owner может скопировать и передать вне email-канала."
        ),
    )
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvitationListResponse(BaseModel):
    """``GET /trees/{tree_id}/invitations`` — pending + recent invitations."""

    tree_id: uuid.UUID
    items: list[InvitationResponse]


class InvitationAcceptRequest(BaseModel):
    """Тело ``POST /invitations/{token}/accept``.

    Phase 11.0 не использует body — accept происходит через path token + текущего
    user'а из auth-stub'а. Резервируем класс для будущих полей (например,
    «принять и сразу включить notification preferences»).
    """


class InvitationAcceptResponse(BaseModel):
    """Ответ на accept: возвращает membership и tree_id для UI redirect'а."""

    tree_id: uuid.UUID
    membership_id: uuid.UUID
    role: str


class MemberResponse(BaseModel):
    """Read-модель active membership."""

    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    invited_by: uuid.UUID | None = None
    joined_at: datetime = Field(
        description=(
            "Когда membership стал активным. Для backfilled OWNER-row "
            "(миграция Phase 11.0) — ``created_at`` (момент применения миграции)."
        ),
    )
    revoked_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MemberListResponse(BaseModel):
    """``GET /trees/{tree_id}/members`` — все active memberships дерева."""

    tree_id: uuid.UUID
    items: list[MemberResponse]


class MemberRoleUpdateRequest(BaseModel):
    """``PATCH /memberships/{id}`` — изменение роли.

    Запрещено демоутить себя из OWNER в EDITOR/VIEWER (нужен сначала transfer).
    Запрещено вешать второй OWNER через UPDATE — DB-уровневый partial unique
    отвергнет. Значит, для смены OWNER используется отдельная transfer-семантика
    (Phase 11.1, не в этом PR).
    """

    role: Literal["editor", "viewer"]


# =============================================================================
# Phase 11.1 — audit log + owner transfer + invitation resend.
# =============================================================================


class AuditLogEntry(BaseModel):
    """Один read-only ряд из ``audit_log`` для UI sharing-history."""

    id: uuid.UUID
    entity_type: str = Field(
        description="ORM-class имя — `tree_memberships`, `tree_invitations`, ..."
    )
    entity_id: uuid.UUID
    action: str = Field(description="`insert` | `update` | `delete` | `restore` | `merge`")
    actor_user_id: uuid.UUID | None = None
    actor_kind: str
    reason: str | None = None
    diff: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogPage(BaseModel):
    """``GET /trees/{id}/audit-log`` — paginated."""

    tree_id: uuid.UUID
    total: int
    limit: int
    offset: int
    items: list[AuditLogEntry]


class TransferOwnerRequest(BaseModel):
    """``PATCH /trees/{id}/transfer-owner`` — 2-of-2 confirmation pattern.

    Чтобы случайный клик не унёс ownership навсегда, мы требуем явное
    подтверждение **обоих** email-адресов: текущего owner'а (caller) и
    нового. ``new_owner_email`` должен совпадать с email одного из
    активных members; ``current_owner_email_confirmation`` — с email
    caller'а (чтобы UI form'а пропустила «введите свой email чтобы
    подтвердить»).

    Не делает ничего без явного membership-row нового owner'а — нельзя
    transfer'нуть на email, который ещё не accept'нул invitation.
    """

    new_owner_email: str = Field(min_length=3, max_length=254)
    current_owner_email_confirmation: str = Field(min_length=3, max_length=254)


class TransferOwnerResponse(BaseModel):
    """Result of ownership transfer."""

    tree_id: uuid.UUID
    previous_owner_user_id: uuid.UUID
    new_owner_user_id: uuid.UUID
    transferred_at: datetime


class InvitationResendResponse(BaseModel):
    """``POST /trees/invitations/{token}/resend`` — rate-limited 1/hour per token."""

    invitation_id: uuid.UUID
    invitee_email: str
    resent_at: datetime
    next_resend_allowed_at: datetime = Field(
        description=(
            "Earliest UTC moment when another resend on this token будет принят. "
            "До этого момента resend возвращает 429."
        ),
    )
