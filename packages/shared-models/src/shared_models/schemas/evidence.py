"""Pydantic-схемы для off-catalog evidence (Phase 22.5).

Provenance — chain-of-custody метаданные о том, *как* был получен
документ-источник, отдельно от ``document_type`` (что это за документ).

Жёсткая Pydantic-форма выбрана потому, что свободный JSONB провенанс
(как у ``ProvenanceMixin``) уже показал себя проблемным: невозможно
агрегировать ``cost_usd`` для ROI-дашборда, нельзя надёжно фильтровать
по ``channel``. См. ADR-0071.

``Provenance.channel == UNKNOWN`` зарезервирован для backfill — для
свежесозданных evidence application-layer обязан проверять, что канал
явный (``Provenance.is_explicit_channel()``).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared_models.enums import DocumentType, ProvenanceChannel


class Provenance(BaseModel):
    """Chain-of-custody метаданные одного evidence-row (Phase 22.5).

    Поля:
        channel: Как документ был получен. ``UNKNOWN`` — backfill-only.
        cost_usd: Сколько стоило получение (None — бесплатно/без оплаты).
        request_date / response_date: Окно паспортного запроса для
            оценки latency архивных каналов.
        jurisdiction: ISO 3166-1 alpha-2 код страны, чьё
            ведомство/архив выдал документ.
        archive_name: Свободная строка-описание архива/конторы.
        intermediary: Имя/контакт посредника (только при
            ``channel == PAID_INTERMEDIARY``).
        request_reference: Внутренний номер дела архива, FOIA-ID и
            аналоги — для повторной заявки и audit-trail.
        notes: Свободные заметки. Не для PII.
        migrated: ``True`` для строк, созданных backfill-миграцией
            Phase 22.5. UI и aggregations могут фильтровать.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    channel: ProvenanceChannel
    cost_usd: Decimal | None = None
    request_date: dt.date | None = None
    response_date: dt.date | None = None
    jurisdiction: str | None = Field(default=None, min_length=2, max_length=2)
    archive_name: str | None = Field(default=None, max_length=200)
    intermediary: str | None = Field(default=None, max_length=200)
    request_reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    migrated: bool = False

    @field_validator("cost_usd")
    @classmethod
    def _cost_non_negative(cls, value: Decimal | None) -> Decimal | None:
        """``cost_usd`` ≥ 0 (None — отсутствие платежа, не ошибка)."""
        if value is not None and value < 0:
            msg = "cost_usd must be non-negative"
            raise ValueError(msg)
        return value

    @field_validator("jurisdiction")
    @classmethod
    def _jurisdiction_uppercase(cls, value: str | None) -> str | None:
        """ISO 3166-1 alpha-2 — две заглавные буквы."""
        if value is None:
            return None
        if not value.isalpha() or not value.isupper():
            msg = "jurisdiction must be ISO 3166-1 alpha-2 (uppercase letters)"
            raise ValueError(msg)
        return value

    def is_explicit_channel(self) -> bool:
        """Channel явно указан (не ``UNKNOWN``).

        Используется API-валидаторами POST/PATCH: при создании evidence
        каналом обязан быть один из реальных вариантов; ``UNKNOWN``
        допустим только для backfill-строк (отмечены ``migrated=True``).
        """
        return self.channel is not ProvenanceChannel.UNKNOWN


def default_unknown_provenance() -> dict[str, object]:
    """Дефолт provenance для backfill: ``{channel: unknown, migrated: true}``.

    Используется alembic-миграцией и server_default колонки. Не
    предназначен для нового user input — application-layer обязан
    отвергать ``channel == UNKNOWN`` для свежих записей.
    """
    return {"channel": ProvenanceChannel.UNKNOWN.value, "migrated": True}


__all__ = [
    "DocumentType",
    "Provenance",
    "ProvenanceChannel",
    "default_unknown_provenance",
]
