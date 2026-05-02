"""Idempotent upsert: typed records → seed tables.

Postgres ``ON CONFLICT (natural_pk) DO UPDATE`` semantics. Re-running
ingest на уже-ingested seed = same row count, updated_at refreshes.

Каждая ``upsert_*`` функция:

* принимает open async session + список records;
* выполняет один bulk insert..on_conflict_do_update;
* возвращает :class:`UpsertCounts` для CLI reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shared_models.orm import (
    CountryArchiveDirectorySeed,
    FabricationPatternSeed,
    PlaceLookupSeed,
    SurnameTransliterationSeed,
    SurnameVariantSeed,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from seed_data.loaders import (
        CountryRecord,
        FabricationPatternRecord,
        PlaceLookupRecord,
        SurnameClusterRecord,
        TransliterationRecord,
    )


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    """Result summary from upsert_* (best-effort: PG ON CONFLICT не сообщает new vs existing).

    ``total`` — input records, ``written`` — rows after upsert (insert
    OR update). PostgreSQL ON CONFLICT DO UPDATE не различает inserted-vs-
    updated в одном statement; для diagnostic-точности используется
    pre-count + post-count в CLI integration tests, не здесь.
    """

    table: str
    total: int


async def _upsert(
    session: AsyncSession,
    *,
    model: Any,
    records: list[dict[str, Any]],
    pk_columns: list[str],
    update_columns: list[str],
) -> int:
    """Generic bulk upsert helper.

    ``records`` — list of column-value dicts (already serialized). Returns
    number of rows attempted. Returns early на empty list (одно SQL-statement
    skipped).
    """
    if not records:
        return 0
    from sqlalchemy import func  # noqa: PLC0415

    stmt = pg_insert(model).values(records)
    # ON CONFLICT DO UPDATE: rewrite каждую update_columns + force-refresh
    # updated_at via SQL-side now() (server_default срабатывает только на
    # INSERT, не на UPDATE — manual now() гарантирует свежий timestamp).
    set_map: dict[str, Any] = {col: stmt.excluded[col] for col in update_columns}
    set_map["updated_at"] = func.now()
    stmt = stmt.on_conflict_do_update(index_elements=pk_columns, set_=set_map)
    await session.execute(stmt)
    return len(records)


async def upsert_countries(session: AsyncSession, records: list[CountryRecord]) -> UpsertCounts:
    payload = [
        {
            "iso2": r.iso2,
            "country": r.country,
            "v2_batch": r.v2_batch,
            "raw_data": r.raw_data,
        }
        for r in records
    ]
    n = await _upsert(
        session,
        model=CountryArchiveDirectorySeed,
        records=payload,
        pk_columns=["iso2"],
        update_columns=["country", "v2_batch", "raw_data"],
    )
    return UpsertCounts(table="country_archive_directory_seed", total=n)


async def upsert_surnames(
    session: AsyncSession, records: list[SurnameClusterRecord]
) -> UpsertCounts:
    payload = [
        {
            "canonical": r.canonical,
            "community_scope": r.community_scope,
            "rank_within_scope": r.rank_within_scope,
            "variants_latin": list(r.variants_latin),
            "variants_cyrillic": r.variants_cyrillic,
            "variants_hebrew": r.variants_hebrew,
            "variants_yiddish": r.variants_yiddish,
            "raw_data": r.raw_data,
        }
        for r in records
    ]
    n = await _upsert(
        session,
        model=SurnameVariantSeed,
        records=payload,
        pk_columns=["canonical", "community_scope"],
        update_columns=[
            "rank_within_scope",
            "variants_latin",
            "variants_cyrillic",
            "variants_hebrew",
            "variants_yiddish",
            "raw_data",
        ],
    )
    return UpsertCounts(table="surname_variant_seed", total=n)


async def upsert_transliterations(
    session: AsyncSession, records: list[TransliterationRecord]
) -> UpsertCounts:
    payload = [
        {
            "source_form": r.source_form,
            "target_forms": r.target_forms,
            "raw_data": r.raw_data,
        }
        for r in records
    ]
    n = await _upsert(
        session,
        model=SurnameTransliterationSeed,
        records=payload,
        pk_columns=["source_form"],
        update_columns=["target_forms", "raw_data"],
    )
    return UpsertCounts(table="surname_transliteration_seed", total=n)


async def upsert_fabrication_patterns(
    session: AsyncSession, records: list[FabricationPatternRecord]
) -> UpsertCounts:
    payload = [
        {
            "pattern_id": r.pattern_id,
            "category": r.category,
            "description": r.description,
            "detection_rule": r.detection_rule,
            "confidence_when_flagged": r.confidence_when_flagged,
            "raw_data": r.raw_data,
        }
        for r in records
    ]
    n = await _upsert(
        session,
        model=FabricationPatternSeed,
        records=payload,
        pk_columns=["pattern_id"],
        update_columns=[
            "category",
            "description",
            "detection_rule",
            "confidence_when_flagged",
            "raw_data",
        ],
    )
    return UpsertCounts(table="fabrication_pattern_seed", total=n)


async def upsert_places(session: AsyncSession, records: list[PlaceLookupRecord]) -> UpsertCounts:
    payload = [
        {
            "old_name": r.old_name,
            "modern_country": r.modern_country,
            "old_name_local": r.old_name_local,
            "modern_name": r.modern_name,
            "lat": r.lat,
            "lon": r.lon,
            "coordinate_precision": r.coordinate_precision,
            "raw_data": r.raw_data,
        }
        for r in records
    ]
    n = await _upsert(
        session,
        model=PlaceLookupSeed,
        records=payload,
        pk_columns=["old_name", "modern_country"],
        update_columns=[
            "old_name_local",
            "modern_name",
            "lat",
            "lon",
            "coordinate_precision",
            "raw_data",
        ],
    )
    return UpsertCounts(table="place_lookup_seed", total=n)


__all__ = [
    "UpsertCounts",
    "upsert_countries",
    "upsert_fabrication_patterns",
    "upsert_places",
    "upsert_surnames",
    "upsert_transliterations",
]
