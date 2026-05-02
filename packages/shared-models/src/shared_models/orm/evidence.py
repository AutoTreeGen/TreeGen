"""Evidence + DocumentTypeWeight — off-catalog evidence with split provenance (Phase 22.5).

См. ADR-0071 «Separate evidence weight from provenance».

Mental-model: исторически у нас был «один confidence-числовой score»,
в котором путались две разные оси:

* *насколько силён сам документ* (паспорт vs. публичное дерево),
* *как именно мы его получили* (личный визит в архив vs. купили
  у посредника vs. unknown — backfill).

Phase 22.5 разделяет их:

* ``Evidence.document_type`` — что это за документ (enum). Через
  data-driven lookup-таблицу ``DocumentTypeWeight`` он отображается
  в **weight ∈ {1,2,3}** (tier). Lookup, не функция в коде, чтобы
  не-инженерная команда могла переоценивать классификацию без
  деплоя.
* ``Evidence.provenance`` — JSONB строгой формы (Pydantic
  :class:`shared_models.schemas.evidence.Provenance`): channel,
  cost_usd, archive_name, request_reference, … Audit-trail и
  ROI-tracking (Phase 22.4).
* ``Evidence.confidence`` — derived score, recompute'ится из
  ``weight × match_certainty`` SQLAlchemy-event'ом перед flush.

Service-table pattern: не наследует ``TreeEntityMixins`` —
``provenance`` здесь *strict-shape*, а не свободный JSONB
``ProvenanceMixin``. Audit-listener для status-полей не нужен:
Evidence — артефакт исследования, lifecycle бесхитростный
(create → soft-delete если ошибка). См. SERVICE_TABLES allowlist.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import DocumentType
from shared_models.mixins import IdMixin, TimestampMixin


class DocumentTypeWeight(Base):
    """Lookup-таблица tier-веса для каждого ``DocumentType`` (Phase 22.5).

    PK — сам ``document_type`` (text). ``weight`` ∈ {1,2,3} (tier-N).
    Seed-данные в alembic-миграции 0033; обновлять допустимо in-place
    (UPDATE) — это reference data, не пользовательский факт.

    Почему таблица, а не словарь в коде: переоценка tier'а должна быть
    видна без деплоя. Например, если архивный союз решит, что
    ``revision_list_entry`` теперь tier-1 (а не tier-1 как сейчас),
    update в одну строку, без миграции.
    """

    __tablename__ = "document_type_weights"
    __table_args__ = (CheckConstraint("weight IN (1, 2, 3)", name="ck_doc_type_weight_tier"),)

    document_type: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)


class Evidence(IdMixin, TimestampMixin, Base):
    """Off-catalog evidence-row: документ + provenance + derived confidence.

    Поля:
        tree_id: FK ``trees.id ON DELETE CASCADE`` — evidence-row живёт
            только в контексте дерева. GDPR-erasure ADR-0049 чистит
            evidence вместе с деревом.
        source_id: Опциональный FK ``sources.id ON DELETE SET NULL`` —
            ссылка на документ-source если он отдельно зарегистрирован.
            NULL допустим: бывают evidence без формального source-row
            (oral_testimony, family letter без даты).
        entity_type / entity_id: Полиморфная привязка к доменной
            сущности (person/family/event), как у ``Citation``. Без
            FK — целостность на уровне приложения.
        document_type: ``DocumentType`` enum (text); дефолт ``other``.
            NULL не допускаем — backfill подставит ``other``.
        match_certainty: Сила совпадения evidence с этим именно entity
            (0..1). Заполняется caller'ом по ситуации. Дефолт 0.5.
        confidence: Derived = ``weight × match_certainty``, где
            weight берётся из ``document_type_weights``. Не задаётся
            пользователем напрямую; recompute через SQLAlchemy event
            ``before_insert`` / ``before_update``. Формально может
            превышать 1.0 (max=3.0); потребители нормализуют по своим
            нуждам, см. ADR-0071 §«weight semantics».
        provenance: JSONB strict-shape (Pydantic
            :class:`shared_models.schemas.evidence.Provenance`).
            Дефолт — ``{channel: unknown, migrated: true}``: server-
            default подставится, application-layer обязан передать
            явный channel при `INSERT`.
        deleted_at: Soft-delete (без ``SoftDeleteMixin``, чтобы
            audit-listener не аудитил Evidence как domain-факт —
            это исследовательский артефакт).
    """

    __tablename__ = "evidence"
    __table_args__ = (
        # confidence ≥ 0; верхняя граница не фиксируется (max=3 при
        # weight=3 × match_certainty=1).
        CheckConstraint("confidence >= 0", name="ck_evidence_confidence_non_negative"),
        # match_certainty в [0,1] — guard.
        CheckConstraint(
            "match_certainty >= 0 AND match_certainty <= 1",
            name="ck_evidence_match_certainty_range",
        ),
        # Provenance JSONB обязан содержать ``channel``; sanity-check
        # на DB-уровне поверх Pydantic-валидации.
        CheckConstraint(
            "provenance ? 'channel'",
            name="ck_evidence_provenance_has_channel",
        ),
        # Полиморфный поиск evidence по entity (UI «карточка персоны:
        # все её evidence»).
        Index("ix_evidence_entity", "entity_type", "entity_id"),
        # Tree-scoped list view — сортировка по confidence DESC внутри
        # дерева (Phase 22.4 ROI dashboard).
        Index("ix_evidence_tree_confidence", "tree_id", "confidence"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_type: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("document_type_weights.document_type", ondelete="RESTRICT"),
        nullable=False,
        default=DocumentType.OTHER.value,
        server_default=DocumentType.OTHER.value,
    )
    match_certainty: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.5,
        server_default=text("0.5"),
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0"),
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"channel": "unknown", "migrated": True},
        server_default=text("""'{"channel": "unknown", "migrated": true}'::jsonb"""),
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


# ---------------------------------------------------------------------------
# Confidence recompute — SQLAlchemy event listener
# ---------------------------------------------------------------------------

# Кэш document_type → weight, чтобы не делать SELECT на каждый flush.
# Заполняется лениво в ``_lookup_weight``. Инвалидация — через
# :func:`reset_document_type_weight_cache` (вызвать после UPDATE
# document_type_weights в той же транзакции, что и Evidence-flush).
_WEIGHT_CACHE: dict[str, int] = {}


def reset_document_type_weight_cache() -> None:
    """Сбросить кэш ``document_type → weight``.

    Вызвать, если ``document_type_weights`` обновлялся (тестовая
    фикстура / админская переоценка tier'а). В обычном runtime
    кэш стабилен — seed данные иммутабельны после миграции.
    """
    _WEIGHT_CACHE.clear()


def _lookup_weight(session: Any, document_type_value: str) -> int:
    """Прочитать weight для ``document_type`` из БД (с локальным кэшем).

    Принимает SQLAlchemy session (sync или async-sync facade) и
    делает SELECT в ту же транзакцию. Если значение не найдено —
    возвращает 3 (tier-3, дефолт для ``other``); это safety-net на
    случай, если миграция не успела засеять lookup-таблицу.
    """
    cached = _WEIGHT_CACHE.get(document_type_value)
    if cached is not None:
        return cached
    row = session.execute(
        text("SELECT weight FROM document_type_weights WHERE document_type = :dt"),
        {"dt": document_type_value},
    ).first()
    weight = int(row[0]) if row else 3
    _WEIGHT_CACHE[document_type_value] = weight
    return weight


@event.listens_for(Evidence, "before_insert")
@event.listens_for(Evidence, "before_update")
def _recompute_confidence(_mapper: Any, connection: Any, target: Evidence) -> None:
    """Перед каждым INSERT/UPDATE пересчитать ``confidence``.

    Формула брифа: ``confidence = weight × match_certainty``. Weight
    берётся из ``document_type_weights`` через connection-level
    SELECT (одна транзакция), чтобы не было drift'а между Evidence
    и кэшем weights. Локальный кэш ``_WEIGHT_CACHE`` ускоряет
    последовательные flush'и в одной сессии.
    """
    weight = _lookup_weight(connection, target.document_type)
    target.confidence = float(weight) * float(target.match_certainty)


__all__ = [
    "DocumentTypeWeight",
    "Evidence",
    "reset_document_type_weight_cache",
]
