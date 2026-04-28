"""Pydantic-схемы запросов и ответов dna-service.

Принципиально: никаких полей, которые могут содержать raw DNA
(genotype, rsid, position) — см. ADR-0020 §«Privacy guards».
Aggregate-only metadata.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ConsentCreate(BaseModel):
    """Запрос на создание consent record."""

    model_config = ConfigDict(extra="forbid")

    tree_id: uuid.UUID
    user_id: uuid.UUID
    kit_owner_email: EmailStr
    consent_text: str = Field(..., min_length=1)
    consent_version: str = Field(default="1.0", min_length=1, max_length=32)


class ConsentResponse(BaseModel):
    """Aggregate consent metadata (никаких decrypted DNA-данных)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tree_id: uuid.UUID
    user_id: uuid.UUID
    kit_owner_email: EmailStr
    consent_version: str
    consented_at: dt.datetime
    revoked_at: dt.datetime | None
    is_active: bool


class TestRecordResponse(BaseModel):
    """Aggregate metadata для одного загруженного blob'а."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tree_id: uuid.UUID
    consent_id: uuid.UUID
    user_id: uuid.UUID
    size_bytes: int
    sha256: str
    snp_count: int
    provider: str
    encryption_scheme: str
    uploaded_at: dt.datetime


class MatchRequest(BaseModel):
    """Запрос на matching между двумя test_record id."""

    model_config = ConfigDict(extra="forbid")

    test_a_id: uuid.UUID
    test_b_id: uuid.UUID


class MatchSegment(BaseModel):
    """Один shared segment в derived stats — без SNP-уровневых данных."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chromosome: int
    start_bp: int
    end_bp: int
    num_snps: int
    cm_length: float


class MatchRelationship(BaseModel):
    """Один relationship-кандидат с CC-BY attribution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    probability: float
    cm_range: tuple[int, int]
    source: str


class MatchResponse(BaseModel):
    """Aggregate match report для двух test_records."""

    model_config = ConfigDict(extra="forbid")

    test_a_id: uuid.UUID
    test_b_id: uuid.UUID
    test_a_provider: str
    test_b_provider: str
    test_a_snp_count: int
    test_b_snp_count: int
    shared_segments: list[MatchSegment]
    total_shared_cm: float
    longest_segment_cm: float
    relationship_predictions: list[MatchRelationship]
    warnings: list[str]


class KitLinkPersonRequest(BaseModel):
    """PATCH body для линковки kit'а к персоне в дереве (Phase 7.3).

    ``person_id=None`` явно очищает связь (unlink). Пустого payload не
    допускаем, чтобы не путать «не передал» и «обнулить» — caller
    должен явно прислать ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID | None


class KitResponse(BaseModel):
    """Aggregate metadata кита (без raw DNA-данных)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tree_id: uuid.UUID
    owner_user_id: uuid.UUID
    person_id: uuid.UUID | None
    source_platform: str
    external_kit_id: str | None
    display_name: str | None
    ethnicity_population: str


class KitListResponse(BaseModel):
    """Список китов одного пользователя.

    Phase 6.3: фильтрация по ``owner_user_id`` (auth ещё не подключён,
    user_id передаётся query-параметром). Phase 6.x с auth — переедет
    на ``/me/dna-kits``.
    """

    model_config = ConfigDict(extra="forbid")

    owner_user_id: uuid.UUID
    total: int
    items: list[KitResponse]


# ---- Phase 6.3 — match listing / detail / link --------------------------


class DnaMatchListItem(BaseModel):
    """Одна строка в match-list (aggregate-only, без segment-уровней).

    Поле ``matched_person_id`` показывает, привязан ли матч к персоне
    в дереве. ``shared_match_count`` — сколько других matches делят ДНК
    с этим (Leeds clustering input).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    kit_id: uuid.UUID
    tree_id: uuid.UUID
    external_match_id: str | None
    display_name: str | None
    total_cm: float | None
    largest_segment_cm: float | None
    segment_count: int | None
    predicted_relationship: str | None
    confidence: str | None
    shared_match_count: int | None
    matched_person_id: uuid.UUID | None


class DnaMatchListResponse(BaseModel):
    """Постраничный список matches одного kit'а."""

    model_config = ConfigDict(extra="forbid")

    kit_id: uuid.UUID
    total: int
    limit: int
    offset: int
    min_cm: float | None
    items: list[DnaMatchListItem]


class DnaMatchSegmentItem(BaseModel):
    """Один shared segment для chromosome painting.

    ADR-0014 §«Privacy guards»: только агрегаты (chromosome, start_bp,
    end_bp, cM, num_snps), никаких rsid/genotype-уровней. Реальное
    хранение — в ``DnaMatch.provenance['segments']`` (см. ADR-0033).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chromosome: int
    start_bp: int
    end_bp: int
    cm: float
    num_snps: int | None = None


class DnaSharedAncestorHint(BaseModel):
    """Подсказка про общего предка, если bу UI алгоритм или пользователь его записал.

    Persisted в ``DnaMatch.provenance['shared_ancestor_hint']`` —
    строго opt-in, не используется как evidence без явного review
    (ADR-0033 §«Hints — это не facts»).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    person_id: uuid.UUID | None = None
    source: str | None = None  # "user_note" | "automatic" | внешняя платформа


class DnaMatchDetailResponse(DnaMatchListItem):
    """Детальная карточка match: list-поля + chromosome painting."""

    model_config = ConfigDict(extra="forbid")

    notes: str | None
    segments: list[DnaMatchSegmentItem]
    shared_ancestor_hint: DnaSharedAncestorHint | None


class DnaMatchLinkRequest(BaseModel):
    """PATCH body для линковки match → person в дереве.

    Body симметричен ``KitLinkPersonRequest``, но передаём `tree_id` явно
    для usability фронта (он знает active tree, не делает extra fetch).
    Сервер всё равно проверяет, что ``person.tree_id == match.tree_id``.
    """

    model_config = ConfigDict(extra="forbid")

    tree_id: uuid.UUID
    person_id: uuid.UUID
