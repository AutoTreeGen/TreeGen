"""DnaMatch — одна строка из match-list какого-то kit'а.

В Ancestry/MyHeritage match — это другой человек, с которым ты делишь ДНК.
``shared_matches`` (m2m) — связи match-match через таблицу SharedMatch.

Phase 16.3 (ADR-0072): добавлены ``platform`` (denormalized из
``DnaKit.source_platform`` для прямой фильтрации), ``match_username``
(когда платформа отдаёт username отдельно от display_name),
``raw_payload`` (полная CSV-row как JSONB для re-parse при эволюции
схемы), ``predicted_relationship_normalized``
(:class:`PredictedRelationship` enum bucket рядом с raw-text),
``resolution_confidence`` (float-confidence для 16.5 cross-platform
resolver, отдельно от существующего legacy ``confidence`` text-поля).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import TreeEntityMixins


class DnaMatch(TreeEntityMixins, Base):
    """Один match в match-list юзера.

    Имена полей выровнены с Ancestry CSV (см. dna-analysis/parsers).
    ``predicted_relationship`` — то что платформа сама прислала
    (e.g. "3rd cousin"). Наша оценка родства — отдельно, в hypotheses
    (Phase 8). Phase 16.3 добавил ``predicted_relationship_normalized``
    — bucket из :class:`PredictedRelationship` для cross-platform
    aggregation; raw-text всё равно сохраняется.
    """

    __tablename__ = "dna_matches"

    kit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_kits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Phase 16.3: denormalized из DnaKit.source_platform — позволяет
    # фильтровать GET /dna/matches?platform= одним indexed-предикатом
    # без join'а на dna_kits. Согласованность поддерживается на уровне
    # импортного pipeline'а (всегда копируем из kit'а при INSERT).
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    external_match_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Phase 16.3: некоторые платформы (23andMe) дают отдельный username
    # помимо display_name. Nullable — большинство экспортов не имеет.
    match_username: Mapped[str | None] = mapped_column(String, nullable=True)

    # cM stats — основа для всех оценок родства.
    total_cm: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    largest_segment_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    segment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    predicted_relationship: Mapped[str | None] = mapped_column(String, nullable=True)
    # Phase 16.3: нормализованный bucket из PredictedRelationship enum.
    # Хранится как text (как и остальные enum'ы в БД) — value один из
    # PredictedRelationship.value. Раздельно с raw-string полем для
    # backward-compat и round-trip.
    predicted_relationship_normalized: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Phase 16.3: numeric-confidence для 16.5 cross-platform resolver.
    # Оставляем legacy ``confidence`` (str — platform-side label типа
    # «extremely high») как есть — это разные слои.
    resolution_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    shared_match_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Привязка к персоне в дереве (если match идентифицирован).
    matched_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Phase 16.3: полная CSV-row как JSONB. Source of truth — если
    # схема импортного парсера эволюционирует, можно переразобрать
    # из raw без повторного скачивания экспорта (anti-drift §«preserve
    # raw_payload always»).
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    notes: Mapped[str | None] = mapped_column(String, nullable=True)
