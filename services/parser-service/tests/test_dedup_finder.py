"""Интеграционные тесты dedup_finder сервиса.

Использует тот же ``app_client`` + ``postgres_dsn`` фикстуры из
conftest.py (testcontainers). Импортируем GED-фикстуры через API,
потом вызываем dedup_finder напрямую через AsyncSession.

Главное правило (CLAUDE.md §5 + ADR-0015): dedup_finder — READ-ONLY.
Тест ``test_no_database_mutations`` явно проверяет, что после вызова
любого find_*_duplicates количество строк во всех затронутых таблицах
не меняется.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


# Минимальный GED с одной парой possibly-duplicate persons (одинаковое
# имя в разных транслитерациях, одинаковая дата + место рождения).
# Source и place разные xref'ы, чтобы дать dedup'у работу при двойном
# импорте.
_GED_DEDUP = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Meir /Zhitnitzky/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
0 @I2@ INDI
1 NAME Meir /Zhytnicki/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 @S1@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
0 TRLR
"""


_GED_SIMPLE = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
0 @S1@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
0 TRLR
"""


async def _import_ged(app_client, ged_bytes: bytes) -> uuid.UUID:
    files = {"file": ("test.ged", ged_bytes, "application/octet-stream")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["tree_id"])


async def _make_session(postgres_dsn: str):  # pragma: no cover — helper
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_dsn, future=True)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
    return engine, SessionMaker


@pytest.mark.asyncio
async def test_find_person_duplicates_with_transliterated_surname(app_client, postgres_dsn) -> None:
    """Главный success signal: Zhitnitzky / Zhytnicki → ≥ threshold."""
    from parser_service.services.dedup_finder import find_person_duplicates

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            suggestions = await find_person_duplicates(session, tree_id, threshold=0.80)
            assert suggestions, "expected at least one person duplicate suggestion"
            top = suggestions[0]
            assert top.entity_type == "person"
            assert top.confidence >= 0.80, f"top confidence {top.confidence}"
            # Components должны включать phonetic/levenshtein/birth_year/birth_place.
            assert "phonetic" in top.components
            assert top.components["phonetic"] == 1.0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_find_place_duplicates_after_double_import(app_client, postgres_dsn) -> None:
    """В _GED_DEDUP два разных PLAC: «Slonim, Grodno, Russian Empire» и «Slonim».

    place_match_score должен дать ≥0.80 благодаря prefix-subset boost.
    """
    from parser_service.services.dedup_finder import find_place_duplicates

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            suggestions = await find_place_duplicates(session, tree_id, threshold=0.80)
            assert suggestions, "expected place duplicates between Slonim variants"
            top = suggestions[0]
            assert top.entity_type == "place"
            assert top.confidence >= 0.80
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_find_source_duplicates_after_double_import(app_client, postgres_dsn) -> None:
    """Импорт _GED_SIMPLE дважды → один и тот же SOUR появляется в двух деревьях.

    Sources внутри одного дерева — после одного _GED_SIMPLE их 1.
    Чтобы получить duplicates, импортируем тот же GED дважды в один tree
    (через scriptовый путь). Поскольку POST /imports создаёт новое tree
    на каждый загруженный файл, мы просим API дважды и проверяем, что
    sources внутри ОДНОГО tree теперь 0 пар (т.к. деревья разные).

    Этот тест проверяет другое: разные SOUR-records с похожими title в
    одном дереве сворачиваются.
    """
    from parser_service.services.dedup_finder import find_source_duplicates
    from shared_models.orm import Source
    from sqlalchemy import insert

    tree_id = await _import_ged(app_client, _GED_SIMPLE)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        # Добавим вручную второй похожий source в то же дерево —
        # имитация повторного импорта от другой платформы (Ancestry vs
        # MyHeritage).
        async with SessionMaker() as session:
            await session.execute(
                insert(Source).values(
                    id=uuid.uuid4(),
                    tree_id=tree_id,
                    title="Lubelskie Parish 1838",
                    author="Lubelskie Archive",
                    source_type="other",
                    status="probable",
                    confidence_score=0.5,
                    version_id=1,
                    provenance={"manual": True},
                )
            )
            await session.commit()

        async with SessionMaker() as session:
            suggestions = await find_source_duplicates(session, tree_id, threshold=0.80)
            assert suggestions, "expected source duplicates for similar titles"
            top = suggestions[0]
            assert top.entity_type == "source"
            assert top.confidence >= 0.80
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_threshold_filters_low_confidence(app_client, postgres_dsn) -> None:
    """Threshold 0.99 должен отсечь даже хорошие matches."""
    from parser_service.services.dedup_finder import find_person_duplicates

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            high = await find_person_duplicates(session, tree_id, threshold=0.99)
            low = await find_person_duplicates(session, tree_id, threshold=0.50)
            assert len(low) >= len(high)
            for sug in high:
                assert sug.confidence >= 0.99
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_blocking_and_naive_return_same_pairs(app_client, postgres_dsn) -> None:
    """use_blocking=True и use_blocking=False должны возвращать одинаковые suggestions.

    Это invariant: blocking — оптимизация, не должна терять пары
    (для small trees, где DM-buckets хорошо покрывают всех кандидатов).
    """
    from parser_service.services.dedup_finder import find_person_duplicates

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            with_blocking = await find_person_duplicates(
                session, tree_id, threshold=0.50, use_blocking=True
            )
            naive = await find_person_duplicates(
                session, tree_id, threshold=0.50, use_blocking=False
            )
            # Сравним множества пар (id_a, id_b) — порядок может отличаться.
            blocking_pairs = {
                tuple(sorted([str(s.entity_a_id), str(s.entity_b_id)])) for s in with_blocking
            }
            naive_pairs = {tuple(sorted([str(s.entity_a_id), str(s.entity_b_id)])) for s in naive}
            assert blocking_pairs == naive_pairs
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_database_mutations(app_client, postgres_dsn) -> None:
    """READ-ONLY contract (CLAUDE.md §5 + ADR-0015): dedup_finder не пишет в БД.

    Считаем строки в persons / sources / places до и после серии вызовов
    find_*_duplicates — должны совпадать ровно.
    """
    from parser_service.services.dedup_finder import (
        find_person_duplicates,
        find_place_duplicates,
        find_source_duplicates,
    )
    from shared_models.orm import Person, Place, Source
    from sqlalchemy import func, select

    tree_id = await _import_ged(app_client, _GED_DEDUP)

    engine, SessionMaker = await _make_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            counts_before = {
                "persons": await session.scalar(
                    select(func.count(Person.id)).where(Person.tree_id == tree_id)
                ),
                "sources": await session.scalar(
                    select(func.count(Source.id)).where(Source.tree_id == tree_id)
                ),
                "places": await session.scalar(
                    select(func.count(Place.id)).where(Place.tree_id == tree_id)
                ),
            }

        async with SessionMaker() as session:
            await find_person_duplicates(session, tree_id, threshold=0.50)
            await find_place_duplicates(session, tree_id, threshold=0.50)
            await find_source_duplicates(session, tree_id, threshold=0.50)

        async with SessionMaker() as session:
            counts_after = {
                "persons": await session.scalar(
                    select(func.count(Person.id)).where(Person.tree_id == tree_id)
                ),
                "sources": await session.scalar(
                    select(func.count(Source.id)).where(Source.tree_id == tree_id)
                ),
                "places": await session.scalar(
                    select(func.count(Place.id)).where(Place.tree_id == tree_id)
                ),
            }
        assert counts_before == counts_after, (
            f"dedup_finder must not mutate DB: before={counts_before}, after={counts_after}"
        )
    finally:
        await engine.dispose()
