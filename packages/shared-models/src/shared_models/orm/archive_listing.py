"""ArchiveListing — каталог off-catalog архивов (Phase 22.1 / ADR-0074).

Foundation для 22.2-22.4: paid intermediary directory, request-letter
generator, cost dashboard. Сама 22.1 — только реестр + read/admin endpoints.

Service-table pattern: не доменная сущность дерева — нет ``tree_id``,
``provenance``, ``version_id``, ``soft-delete``. Записи редактируются
явным admin-CRUD; история изменений живёт в ``audit_log`` (через
register_audit_listeners — applies to всех ORM models).

Origin: владелец заплатил $100 SBU oblast Lviv за паспортный запрос
Naum Katz (Konyukhi/Hrubieszów) — record существует, в online-каталог
не выложен. Phase 22.1 описывает ГДЕ такие записи лежат, чтобы
пользователи знали куда обращаться (и сколько ждать ответа).

Дизайн-выбор: ``record_types`` и ``languages`` — JSONB-массивы, не
join-таблицы. Аргументация в ADR-0074: enum мал (~10 значений),
filter по contains через ``@>`` / ``?``-операторы быстрый, join
overhead не оправдан для read-mostly seed-данных.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class RecordType(enum.StrEnum):
    """Тип архивных записей.

    Sprint-scope значений: гражданская регистрация (рождения/браки/смерти),
    дореволюционные метрические книги, ревизские сказки, паспортные дела
    (внутренний паспорт СССР), военные, партийные/КГБ дела, кладбищенские
    реестры, кадастры, нотариальные акты. ``other`` — для неклассифицируемых
    (личные коллекции архивов, ведомственные архивы и т.п.).
    """

    CIVIL_BIRTH = "civil_birth"
    CIVIL_MARRIAGE = "civil_marriage"
    CIVIL_DEATH = "civil_death"
    METRIC_BOOK = "metric_book"
    REVISION_LIST = "revision_list"
    PASSPORT_INTERNAL = "passport_internal"
    MILITARY = "military"
    PARTY_PERSONAL_FILE = "party_personal_file"
    NKVD_KGB_FILE = "nkvd_kgb_file"
    CEMETERY = "cemetery"
    CADASTRE = "cadastre"
    NOTARIAL = "notarial"
    OTHER = "other"


class AccessMode(enum.StrEnum):
    """Как пользователь может получить доступ к записям.

    - ``ONLINE_CATALOG``: публичный online-поиск (редкость для off-catalog
      архивов — обычно эти записи здесь именно потому, что online их нет).
    - ``IN_PERSON_ONLY``: только личное посещение читального зала.
    - ``PAID_REQUEST``: архив принимает платные официальные запросы
      (типичный путь для SBU oblast, ZAGS, Standesamt).
    - ``INTERMEDIARY_REQUIRED``: записи доступны только через частного
      исследователя; archive напрямую не отвечает иностранцам. Phase 22.2
      будет каталогизировать таких посредников отдельно (с legal review).
    - ``CLOSED``: архив сейчас закрыт (war damage, политика, ремонт).
    """

    ONLINE_CATALOG = "online_catalog"
    IN_PERSON_ONLY = "in_person_only"
    PAID_REQUEST = "paid_request"
    INTERMEDIARY_REQUIRED = "intermediary_required"
    CLOSED = "closed"


class ArchiveListing(IdMixin, TimestampMixin, Base):
    """Один off-catalog архив + типы записей + access mode.

    Nullable почти все поля контактов / диапазонов: реальные архивы часто
    публикуют неполную информацию, лучше иметь запись с ``country + name +
    record_types + last_verified``, чем выдумывать недостающее.

    ``last_verified`` — NOT NULL: claim "this archive holds X records,
    contact via Y" без даты подтверждения недопустим (ADR-0074 §«Honesty
    over coverage»). Если нет даты — не добавляем строку.

    ``record_types`` и ``languages`` хранятся как JSONB-массивы строк
    (значения ``RecordType``/ISO-639-1). Filter по содержанию через
    ``record_types ? 'civil_birth'`` или ``record_types @> '["civil_birth"]'``.
    """

    __tablename__ = "archive_listings"
    __table_args__ = (
        # Главный фильтр UI: «архивы для UA + civil_birth». Country первым,
        # access_mode часто совпадает со столбцом-фильтром (показать только
        # paid_request).
        Index(
            "ix_archive_listings_country_access",
            "country",
            "access_mode",
        ),
        # GIN на record_types — для запросов вида
        # ``WHERE record_types ?| array['civil_birth','metric_book']``.
        Index(
            "ix_archive_listings_record_types_gin",
            "record_types",
            postgresql_using="gin",
        ),
        # ISO 3166-1 alpha-2 — ровно 2 символа, верхний регистр.
        CheckConstraint(
            "country ~ '^[A-Z]{2}$'",
            name="ck_archive_listings_country_iso2",
        ),
        # Если оба года заданы — диапазон валидный.
        CheckConstraint(
            "year_from IS NULL OR year_to IS NULL OR year_to >= year_from",
            name="ck_archive_listings_year_range",
        ),
        # Fee range валидный, если заданы оба.
        CheckConstraint(
            "fee_min_usd IS NULL OR fee_max_usd IS NULL OR fee_max_usd >= fee_min_usd",
            name="ck_archive_listings_fee_range",
        ),
        # Sanity: положительные числа.
        CheckConstraint(
            "fee_min_usd IS NULL OR fee_min_usd >= 0",
            name="ck_archive_listings_fee_min_nonneg",
        ),
        CheckConstraint(
            "fee_max_usd IS NULL OR fee_max_usd >= 0",
            name="ck_archive_listings_fee_max_nonneg",
        ),
        CheckConstraint(
            "typical_response_days IS NULL OR typical_response_days >= 0",
            name="ck_archive_listings_response_days_nonneg",
        ),
        CheckConstraint(
            "privacy_window_years IS NULL OR privacy_window_years >= 0",
            name="ck_archive_listings_privacy_window_nonneg",
        ),
        CheckConstraint(
            "access_mode IN ("
            "'online_catalog', 'in_person_only', 'paid_request', "
            "'intermediary_required', 'closed'"
            ")",
            name="ck_archive_listings_access_mode",
        ),
    )

    # English/латиница — для search & API. ``name_native`` в исходной
    # локали архива (Cyrillic/Polish/German…) — чтобы пользователь мог
    # cite в письме на родном для архива языке.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_native: Mapped[str | None] = mapped_column(String(255), nullable=True)

    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # JSONB-массивы строк. Хранятся как list[str], где элементы — значения
    # RecordType / ISO-639-1 lang codes.
    languages: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    record_types: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    # Год покрытия (inclusive, оба nullable независимо).
    year_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)

    access_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AccessMode.PAID_REQUEST.value,
    )

    # Fee range в USD. Tuple в Pydantic-API → две колонки в DB
    # (Postgres не имеет tuple, range-types overkill для двух чисел).
    fee_min_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_max_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)

    typical_response_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Personal-data window: записи моложе X лет недоступны (typical для
    # civil registry — 75-100 лет).
    privacy_window_years: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NOT NULL — claim "this listing is current as of D" обязательный.
    last_verified: Mapped[dt.date] = mapped_column(Date, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        """Сериализация для Pydantic-схем (response models)."""
        return {
            "id": self.id,
            "name": self.name,
            "name_native": self.name_native,
            "country": self.country,
            "region": self.region,
            "address": self.address,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "website": self.website,
            "languages": list(self.languages or []),
            "record_types": list(self.record_types or []),
            "year_from": self.year_from,
            "year_to": self.year_to,
            "access_mode": self.access_mode,
            "fee_min_usd": self.fee_min_usd,
            "fee_max_usd": self.fee_max_usd,
            "typical_response_days": self.typical_response_days,
            "privacy_window_years": self.privacy_window_years,
            "notes": self.notes,
            "last_verified": self.last_verified,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


__all__ = ["AccessMode", "ArchiveListing", "RecordType"]
