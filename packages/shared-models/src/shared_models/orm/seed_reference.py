"""Reference seed tables — Phase 22.1b / ADR-0081.

Read-only reference data, ingested via the ``seed-data`` workspace package
(``python -m seed_data ingest``). Все 5 таблиц — service-table pattern:

* без ``tree_id`` — данные не привязаны к user-tree (страны, фамилии,
  места, паттерны фабрикации — общие справочники);
* без ``provenance`` / ``version_id`` — это reference data из committed
  JSON-файлов, не genealogy-факт пользователя;
* без ``SoftDeleteMixin`` — обновление = ``ON CONFLICT DO UPDATE``,
  удаление = ручной DELETE при rollback'е (rare event);
* добавлены в SERVICE_TABLES allowlist в test_schema_invariants.

22.1 (#193) ещё не в main; этот PR ingest'ит data в standalone tables.
22.1c (follow-up после #193) добавит cross-references из
``country_archive_directory_seed.iso2`` в ``archive_registry.country_iso2``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base


def _ts_default() -> dt.datetime:
    """Helper для default'а в Pydantic-side; в ORM используем server_default."""
    return dt.datetime.now(dt.UTC)


class CountryArchiveDirectorySeed(Base):
    """Country reference: jurisdictions, archives, online DBs (Phase 22.1b).

    PK = ISO 3166-1 alpha-2 ``iso2``. ``raw_data`` сохраняет полный record
    из ``autotreegen_country_reference_database*.json`` без потерь —
    consumers (22.1c+) делают select on jsonb keys по необходимости.

    ``v2_batch`` помечает row, обновлённый одним из v2-batch файлов
    (``autotreegen_country_reference_database_v2_batch{1..5}_*.json``).
    """

    __tablename__ = "country_archive_directory_seed"

    # Usually ISO 3166-1 alpha-2 (UA, BY, …); v1 file emits composite
    # historical codes like "AT-GAL" (Austria-Galicia) too — column widened
    # to String(8) to accept those without lossy normalization.
    iso2: Mapped[str] = mapped_column(String(8), primary_key=True)
    country: Mapped[str] = mapped_column(String(200), nullable=False)
    v2_batch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SurnameVariantSeed(Base):
    """Eastern-European surname variant clusters (Phase 22.1b).

    Composite PK = (``canonical``, ``community_scope``) — same canonical
    name can appear in different community scopes (e.g. "Cohen" in Ashkenazi
    vs as a regular English surname). 455 clusters across 6 communities в
    seed v0.2.

    Variants stored split by script (latin/cyrillic/hebrew/yiddish) — это
    дешевле для consumers фильтровать, чем парсить ``raw_data`` каждый раз.
    """

    __tablename__ = "surname_variant_seed"
    __table_args__ = (
        PrimaryKeyConstraint("canonical", "community_scope", name="pk_surname_variant_seed"),
    )

    canonical: Mapped[str] = mapped_column(String(200), nullable=False)
    community_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    rank_within_scope: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variants_latin: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    variants_cyrillic: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    variants_hebrew: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    variants_yiddish: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SurnameTransliterationSeed(Base):
    """Per-source-form transliteration table (Phase 22.1b).

    86 entries в seed v0.2 — карта ``source_form (Cyrillic|Hebrew|Yiddish)``
    → ``target_forms (Latin variants)``. Используется как fallback для
    surname normalizer'а когда :class:`SurnameVariantSeed` не покрывает
    раritet форму.
    """

    __tablename__ = "surname_transliteration_seed"

    source_form: Mapped[str] = mapped_column(String(200), primary_key=True)
    target_forms: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FabricationPatternSeed(Base):
    """Fabrication-detection pattern catalog (Phase 22.1b).

    61 pattern across 7 categories в seed v0.2:
    rabbinical_fantasy / royal_descent_fake / pedigree_collapse /
    same_name_different_person / patronymic_confusion /
    cross_religious_confusion / holocaust_reconstruction_error.

    ``confidence_when_flagged`` = (0..1] — насколько надёжен паттерн при
    срабатывании. Consumers (Phase 22.x evidence-validator) умножают на
    собственную heuristic-confidence для финального score'а.
    """

    __tablename__ = "fabrication_pattern_seed"

    pattern_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detection_rule: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_when_flagged: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PlaceLookupSeed(Base):
    """Eastern-European place lookup: old name → modern (Phase 22.1b).

    Composite PK (``old_name``, ``modern_country``) — same old_name может
    map'иться в разные modern_country при погроничной неоднозначности
    (Brest в Belarus vs France).

    505 places в seed v0.2. ``coordinate_precision`` ∈
    {``exact_or_high_confidence`` ~12%, ``verified_city_seed``, ~0%,
    ``approximate_seed`` ~88%} — consumers, использующие lat/lon для
    map-вьюшек, должны учитывать precision: ``approximate_seed`` точки
    могут отстоять от истинного места на 10–50 км и **не подходят для
    automated geo-matching**, только для UI hint'а / manual verification.
    """

    __tablename__ = "place_lookup_seed"
    __table_args__ = (
        PrimaryKeyConstraint("old_name", "modern_country", name="pk_place_lookup_seed"),
    )

    old_name: Mapped[str] = mapped_column(String(200), nullable=False)
    modern_country: Mapped[str] = mapped_column(String(64), nullable=False)
    old_name_local: Mapped[str | None] = mapped_column(String(200), nullable=True)
    modern_name: Mapped[str] = mapped_column(String(200), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    coordinate_precision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "CountryArchiveDirectorySeed",
    "FabricationPatternSeed",
    "PlaceLookupSeed",
    "SurnameTransliterationSeed",
    "SurnameVariantSeed",
]
