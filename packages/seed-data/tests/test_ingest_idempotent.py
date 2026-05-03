"""Integration tests: ingest is idempotent (Phase 22.1b).

Re-running ingest на уже-ingested seed = same row count, ON CONFLICT
DO UPDATE rotates updated_at но не дублирует. Отдельный test покрывает
alembic 0043 up/down — здесь только runtime семантика.

Маркер ``integration`` — testcontainers-postgres стартует один раз
session-scoped.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from seed_data.config import canonical_paths
from seed_data.ingest import (
    upsert_countries,
    upsert_fabrication_patterns,
    upsert_places,
    upsert_surnames,
    upsert_transliterations,
)
from seed_data.loaders import (
    load_countries,
    load_fabrication_patterns,
    load_places,
    load_surnames,
)
from shared_models.orm import (
    CountryArchiveDirectorySeed,
    FabricationPatternSeed,
    PlaceLookupSeed,
    SurnameTransliterationSeed,
    SurnameVariantSeed,
)
from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


pytestmark = pytest.mark.integration


async def _count(session_factory: async_sessionmaker[AsyncSession], model: type) -> int:
    async with session_factory() as session:
        res = await session.execute(select(func.count()).select_from(model))
        return res.scalar_one()


async def test_country_canonical_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    records = load_countries(canonical_paths()["country_v1"])
    async with session_factory() as session:
        await upsert_countries(session, records)
        await session.commit()
    n_first = await _count(session_factory, CountryArchiveDirectorySeed)

    # Second run — same data — row count unchanged.
    async with session_factory() as session:
        await upsert_countries(session, records)
        await session.commit()
    n_second = await _count(session_factory, CountryArchiveDirectorySeed)

    assert n_first == n_second == 14


async def test_fabrication_canonical_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    records = load_fabrication_patterns(canonical_paths()["fabrication"])
    async with session_factory() as session:
        await upsert_fabrication_patterns(session, records)
        await session.commit()
    n_first = await _count(session_factory, FabricationPatternSeed)
    async with session_factory() as session:
        await upsert_fabrication_patterns(session, records)
        await session.commit()
    n_second = await _count(session_factory, FabricationPatternSeed)
    assert n_first == n_second == 61


async def test_surnames_synthetic_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    synthetic_surname_path: Path,
) -> None:
    clusters, translit = load_surnames(synthetic_surname_path)
    async with session_factory() as session:
        await upsert_surnames(session, clusters)
        await upsert_transliterations(session, translit)
        await session.commit()
    cluster_count_1 = await _count(session_factory, SurnameVariantSeed)
    translit_count_1 = await _count(session_factory, SurnameTransliterationSeed)
    async with session_factory() as session:
        await upsert_surnames(session, clusters)
        await upsert_transliterations(session, translit)
        await session.commit()
    cluster_count_2 = await _count(session_factory, SurnameVariantSeed)
    translit_count_2 = await _count(session_factory, SurnameTransliterationSeed)
    assert cluster_count_1 == cluster_count_2 == 10
    assert translit_count_1 == translit_count_2 == 3


async def test_places_synthetic_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    synthetic_places_path: Path,
) -> None:
    records = load_places(synthetic_places_path)
    async with session_factory() as session:
        await upsert_places(session, records)
        await session.commit()
    n_first = await _count(session_factory, PlaceLookupSeed)
    async with session_factory() as session:
        await upsert_places(session, records)
        await session.commit()
    n_second = await _count(session_factory, PlaceLookupSeed)
    assert n_first == n_second == 5


async def test_country_v2_batch_marker_persists(
    session_factory: async_sessionmaker[AsyncSession],
    synthetic_country_extension_path: Path,
) -> None:
    """v2_batch field roundtrips through upsert."""
    records = load_countries(synthetic_country_extension_path, v2_batch="ext_synth_v2")
    async with session_factory() as session:
        await upsert_countries(session, records)
        await session.commit()
    async with session_factory() as session:
        res = await session.execute(
            select(CountryArchiveDirectorySeed.v2_batch).where(
                CountryArchiveDirectorySeed.iso2 == "ZZ"
            )
        )
        v2_batch = res.scalar_one()
    assert v2_batch == "ext_synth_v2"
