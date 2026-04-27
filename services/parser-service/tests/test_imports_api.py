"""Интеграционные тесты ``POST /imports`` + ``GET /imports/{id}``.

Маркеры: ``db`` + ``integration`` — пропускаются если testcontainers нет.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


_MINIMAL_GED = b"""\
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
2 SOUR @S1@
3 PAGE p. 42
3 QUAY 3
0 @I2@ INDI
1 NAME Mary /Smith/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1875
2 PLAC Vilna, Russian Empire
0 @S1@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
0 TRLR
"""


@pytest.mark.asyncio
async def test_post_import_creates_job_and_persons(app_client) -> None:
    """Загрузка минимального .ged → 201 + status=succeeded + 2 persons."""
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    response = await app_client.post("/imports", files=files)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["stats"]["persons"] == 2
    assert body["stats"]["families"] == 1
    # 1 BIRT (I1) + 1 MARR (F1) = 2 events.
    # Participants: BIRT принципал (1) + MARR husband + wife (2) = 3.
    assert body["stats"]["events"] == 2
    assert body["stats"]["event_participants"] == 3
    # BIRT и MARR ссылаются на разные PLAC → 2 уникальных места.
    assert body["stats"]["places"] == 2
    # Один SOUR-record в фикстуре.
    assert body["stats"]["sources"] == 1
    # Одна SOUR-reference из BIRT(@I1@) → одна citation.
    assert body["stats"]["citations"] == 1
    assert body["source_filename"] == "test.ged"
    assert body["tree_id"] is not None
    assert body["id"] is not None


@pytest.mark.asyncio
async def test_import_persists_birth_event_for_first_person(app_client) -> None:
    """После импорта _MINIMAL_GED у первой персоны есть BIRT-событие.

    Проверка идёт через ``GET /persons/{id}``, который джойнит
    events / event_participants.
    """
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert items, "no persons returned"
    # I1 импортирован первым (вставка в порядке итерации document.persons),
    # поэтому первый элемент с created_at-сортировкой — это John /Smith/.
    first = next(p for p in items if p["gedcom_xref"] == "I1")

    detail = await app_client.get(f"/persons/{first['id']}")
    assert detail.status_code == 200
    events = detail.json()["events"]
    birt_events = [e for e in events if e["event_type"] == "BIRT"]
    assert len(birt_events) == 1, f"expected 1 BIRT, got events={events}"
    assert birt_events[0]["date_raw"] == "1850"


@pytest.mark.asyncio
async def test_import_creates_places_and_links_events(app_client, postgres_dsn) -> None:
    """После импорта в ``places`` лежат уникальные PLAC, событие имеет place_id.

    Проверяем напрямую через AsyncSession, поскольку API эндпоинта /places ещё
    нет (Phase 3.4). BIRT у I1 ссылается на "Slonim, Grodno, Russian Empire".
    """
    from shared_models.orm import Event, Place
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201, created.text
    tree_id = created.json()["tree_id"]

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            places = (
                (await session.execute(select(Place).where(Place.tree_id == tree_id)))
                .scalars()
                .all()
            )
            assert len(places) == 2, [p.canonical_name for p in places]

            names = {p.canonical_name for p in places}
            assert "Slonim, Grodno, Russian Empire" in names
            assert "Vilna, Russian Empire" in names

            birth = (
                await session.execute(
                    select(Event).where(
                        Event.tree_id == tree_id,
                        Event.event_type == "BIRT",
                    )
                )
            ).scalar_one()
            assert birth.place_id is not None
            slonim = next(p for p in places if p.canonical_name.startswith("Slonim"))
            assert birth.place_id == slonim.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_persists_sources(app_client, postgres_dsn) -> None:
    """SOUR-записи попадают в `sources` с TITL и AUTH.

    После импорта `_MINIMAL_GED` в дереве должна быть ровно одна запись
    `sources` с title="Lubelskie parish records 1838" и author="Lubelskie
    Archive". Тип source_type — фолбэк OTHER (классификация в Phase 3.4).
    """
    from shared_models.orm import Source
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201, created.text
    tree_id = created.json()["tree_id"]

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            sources = (
                (await session.execute(select(Source).where(Source.tree_id == tree_id)))
                .scalars()
                .all()
            )
            assert len(sources) == 1
            src = sources[0]
            assert src.title == "Lubelskie parish records 1838"
            assert src.author == "Lubelskie Archive"
            assert src.source_type == "other"
            assert src.provenance.get("gedcom_xref") == "S1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_event_has_citation(app_client, postgres_dsn) -> None:
    """BIRT-событие @I1@ имеет citation на @S1@ с PAGE и QUAY.

    Проверяет:
    - ровно 1 citation после импорта (на BIRT-event @I1@);
    - entity_type == "event", source_id ссылается на наш SOUR;
    - page_or_section == "p. 42";
    - quality нормализован: QUAY 3 → 1.0 (3 / 3).
    """
    from shared_models.orm import Citation, Event, Source
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201, created.text
    tree_id = created.json()["tree_id"]

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            citations = (
                (await session.execute(select(Citation).where(Citation.tree_id == tree_id)))
                .scalars()
                .all()
            )
            assert len(citations) == 1
            cit = citations[0]
            assert cit.entity_type == "event"
            assert cit.page_or_section == "p. 42"
            # QUAY 3 → 3/3 = 1.0.
            assert cit.quality == pytest.approx(1.0)

            birth = (
                await session.execute(
                    select(Event).where(
                        Event.tree_id == tree_id,
                        Event.event_type == "BIRT",
                    )
                )
            ).scalar_one()
            assert cit.entity_id == birth.id

            source = (
                await session.execute(select(Source).where(Source.tree_id == tree_id))
            ).scalar_one()
            assert cit.source_id == source.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_marr_has_both_spouses_as_participants(app_client, postgres_dsn) -> None:
    """MARR-событие в FAM имеет ровно husband + wife как participants.

    После импорта `_MINIMAL_GED` находим единственный MARR-event и проверяем,
    что у него два participants: один с role=husband (person_id = I1) и
    второй с role=wife (person_id = I2).
    """
    from shared_models.orm import Event, EventParticipant, Person
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201, created.text
    tree_id = created.json()["tree_id"]

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            marr = (
                await session.execute(
                    select(Event).where(
                        Event.tree_id == tree_id,
                        Event.event_type == "MARR",
                    )
                )
            ).scalar_one()

            participants = (
                (
                    await session.execute(
                        select(EventParticipant).where(EventParticipant.event_id == marr.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(participants) == 2, [(p.role, p.person_id) for p in participants]

            roles = {p.role: p for p in participants}
            assert set(roles) == {"husband", "wife"}

            husband = (
                await session.execute(
                    select(Person).where(Person.tree_id == tree_id, Person.gedcom_xref == "I1")
                )
            ).scalar_one()
            wife = (
                await session.execute(
                    select(Person).where(Person.tree_id == tree_id, Person.gedcom_xref == "I2")
                )
            ).scalar_one()
            assert roles["husband"].person_id == husband.id
            assert roles["wife"].person_id == wife.id
            assert roles["husband"].family_id is None
            assert roles["wife"].family_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_post_import_rejects_non_gedcom_file(app_client) -> None:
    """Файл с расширением не .ged/.gedcom → 400."""
    files = {"file": ("test.txt", b"not a gedcom", "text/plain")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_import_returns_existing_job(app_client) -> None:
    """Создаём job, потом достаём его по id."""
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    job_id = created.json()["id"]

    response = await app_client.get(f"/imports/{job_id}")
    assert response.status_code == 200
    assert response.json()["id"] == job_id


@pytest.mark.asyncio
async def test_get_import_returns_404_for_unknown_job(app_client) -> None:
    """Несуществующий UUID → 404."""
    response = await app_client.get("/imports/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
