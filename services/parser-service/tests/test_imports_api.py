"""Интеграционные тесты импорта GEDCOM в БД (Phase 3 + Phase 3.5).

Phase 3.5: ``POST /imports`` стал async (202 + enqueue), реальный
парсинг + bulk-insert делает worker через ``run_import_job``. Эти
тесты раньше ходили через HTTP с синхронным импортом — теперь
вызывают ``run_import()`` сервис напрямую с testcontainers-Postgres,
оставляя API-уровень за ``test_imports_async.py``.

Маркеры: ``db`` + ``integration`` — пропускаются если testcontainers
не установлены.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

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
1 OBJE @M1@
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
0 @M1@ OBJE
1 FILE photos/john_smith_1850.jpg
1 FORM jpg
1 TITL John Smith portrait, 1850
0 TRLR
"""


async def _run_import_via_runner(postgres_dsn: str, ged_bytes: bytes, filename: str):
    """Helper: создать tempfile и вызвать ``run_import`` напрямую.

    Возвращает (tree_id, stats_dict). Используется тестами вместо
    HTTP POST /imports — после Phase 3.5 endpoint enqueue'ит worker'а,
    а реальный импорт делает ``run_import``.

    Phase 11.0: ``owner_email`` сознательно совпадает с дефолтом
    ``Settings.owner_email`` (см. ``parser_service.config``). До Phase 4.10
    auth-stub fall back'ает на settings.owner_email; чтобы permission-gate
    распознал HTTP-caller'а как owner'а только-что-созданного дерева,
    оба должны указывать на одного и того же User-row (find-or-create по email).
    """
    from parser_service.config import get_settings
    from parser_service.services.import_runner import run_import
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ged") as tmp:
        tmp.write(ged_bytes)
        tmp_path = Path(tmp.name)

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            job = await run_import(
                session,
                tmp_path,
                owner_email=get_settings().owner_email,
                tree_name=Path(filename).stem,
                source_filename=filename,
            )
            await session.commit()
            return job.tree_id, dict(job.stats)
    finally:
        tmp_path.unlink(missing_ok=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_import_persists_unknown_tags(postgres_dsn) -> None:
    """Phase 5.5a: проприетарные теги сохраняются в ``ImportJob.unknown_tags``.

    Этот тест проверяет полный wire-up: парсер квaрантинит ``_FSFTID`` /
    ``_UID`` / ``_CUSTOM`` в ``GedcomDocument.unknown_tags``, runner
    сериализует их через ``model_dump`` и пишет в jsonb-колонку. Без
    этого 5.5b loss simulator потеряет input для своих репортов, и
    round-trip-export станет невозможным.
    """
    from shared_models.orm import ImportJob
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ged_with_props = b"""\
0 HEAD
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 _FSFTID 12345-ABC
1 _UID ABCDEF
0 @I2@ INDI
1 NAME Mary /Smith/
1 _CUSTOM payload
0 TRLR
"""
    tree_id, _stats = await _run_import_via_runner(postgres_dsn, ged_with_props, "props.ged")

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            job = (
                await session.execute(select(ImportJob).where(ImportJob.tree_id == tree_id))
            ).scalar_one()
            assert isinstance(job.unknown_tags, list)
            tags = sorted((b["owner_xref_id"], b["record"]["tag"]) for b in job.unknown_tags)
            assert ("I1", "_FSFTID") in tags
            assert ("I1", "_UID") in tags
            assert ("I2", "_CUSTOM") in tags
            # owner_kind правильно установлен на сериализации.
            kinds = {b["owner_kind"] for b in job.unknown_tags}
            assert kinds == {"individual"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_import_creates_job_and_persons(postgres_dsn) -> None:
    """``run_import`` парсит минимальный .ged и заливает 2 persons + 1 family."""
    _tree_id, stats = await _run_import_via_runner(postgres_dsn, _MINIMAL_GED, "test.ged")
    assert stats["persons"] == 2
    assert stats["families"] == 1
    # 1 BIRT (I1) + 1 MARR (F1) = 2 events.
    # Participants: BIRT принципал (1) + MARR husband + wife (2) = 3.
    assert stats["events"] == 2
    assert stats["event_participants"] == 3
    # BIRT и MARR ссылаются на разные PLAC → 2 уникальных места.
    assert stats["places"] == 2
    # Один SOUR-record в фикстуре.
    assert stats["sources"] == 1
    # Одна SOUR-reference из BIRT(@I1@) → одна citation.
    assert stats["citations"] == 1
    # Один OBJE-record (M1) и одна OBJE-reference от @I1@.
    assert stats["multimedia"] == 1
    assert stats["entity_multimedia"] == 1


@pytest.mark.asyncio
async def test_import_persists_birth_event_for_first_person(app_client, postgres_dsn) -> None:
    """После ``run_import`` у первой персоны есть BIRT-событие.

    Проверка идёт через ``GET /persons/{id}``, который джойнит
    events / event_participants. Сам импорт делается прямым
    вызовом ``run_import``, минуя async-API.
    """
    tree_id, _stats = await _run_import_via_runner(postgres_dsn, _MINIMAL_GED, "test.ged")

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert items, "no persons returned"
    first = next(p for p in items if p["gedcom_xref"] == "I1")

    detail = await app_client.get(f"/persons/{first['id']}")
    assert detail.status_code == 200
    events = detail.json()["events"]
    birt_events = [e for e in events if e["event_type"] == "BIRT"]
    assert len(birt_events) == 1, f"expected 1 BIRT, got events={events}"
    assert birt_events[0]["date_raw"] == "1850"


@pytest.mark.asyncio
async def test_import_creates_places_and_links_events(postgres_dsn) -> None:
    """После импорта в ``places`` лежат уникальные PLAC, событие имеет place_id.

    BIRT у I1 ссылается на "Slonim, Grodno, Russian Empire".
    """
    from shared_models.orm import Event, Place
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, _stats = await _run_import_via_runner(postgres_dsn, _MINIMAL_GED, "test.ged")

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
async def test_import_persists_sources(postgres_dsn) -> None:
    """SOUR-записи попадают в `sources` с TITL и AUTH."""
    from shared_models.orm import Source
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, _stats = await _run_import_via_runner(postgres_dsn, _MINIMAL_GED, "test.ged")

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
async def test_person_has_multimedia(postgres_dsn) -> None:
    """OBJE-запись попадает в `multimedia_objects`, link к @I1@ — в entity_multimedia."""
    from shared_models.orm import EntityMultimedia, MultimediaObject, Person
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, _stats = await _run_import_via_runner(postgres_dsn, _MINIMAL_GED, "test.ged")

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            objects = (
                (
                    await session.execute(
                        select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(objects) == 1
            obj = objects[0]
            assert obj.caption == "John Smith portrait, 1850"
            assert obj.storage_url == "photos/john_smith_1850.jpg"
            assert obj.object_metadata.get("format") == "jpg"
            assert obj.object_metadata.get("gedcom_xref") == "M1"

            links = (
                (
                    await session.execute(
                        select(EntityMultimedia).where(
                            EntityMultimedia.multimedia_id == obj.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(links) == 1
            link = links[0]
            assert link.entity_type == "person"
            person_i1 = (
                await session.execute(
                    select(Person).where(Person.tree_id == tree_id, Person.gedcom_xref == "I1")
                )
            ).scalar_one()
            assert link.entity_id == person_i1.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_event_has_citation(postgres_dsn) -> None:
    """BIRT-событие @I1@ имеет citation на @S1@ с PAGE и QUAY."""
    from shared_models.orm import Citation, Event, Source
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, _stats = await _run_import_via_runner(postgres_dsn, _MINIMAL_GED, "test.ged")

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
            assert cit.quay_raw == 3
            assert cit.quality == pytest.approx(0.95)

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
async def test_marr_has_both_spouses_as_participants(postgres_dsn) -> None:
    """MARR-событие в FAM имеет ровно husband + wife как participants."""
    from shared_models.orm import Event, EventParticipant, Person
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, _stats = await _run_import_via_runner(postgres_dsn, _MINIMAL_GED, "test.ged")

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


# ---------------------------------------------------------------------------
# Phase 3.5 API-surface — оставшиеся HTTP-тесты на новый async-контракт.
# Сами enqueue/202/SSE покрываются ``test_imports_async.py``;
# здесь только базовая валидация, что endpoint всё ещё доступен и
# реджектит мусор.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_import_rejects_non_gedcom_file(app_client) -> None:
    """Файл с расширением не .ged/.gedcom → 400 (до enqueue)."""
    files = {"file": ("test.txt", b"not a gedcom", "text/plain")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_import_returns_404_for_unknown_job(app_client) -> None:
    """Несуществующий UUID → 404."""
    response = await app_client.get("/imports/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Phase 3.5 follow-up: inline OBJE без xref'а. Доменная логика — тестируется
# напрямую через runner, без HTTP.
# ---------------------------------------------------------------------------

_INLINE_OBJE_GED = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 OBJE
2 FILE photos/inline_portrait.jpg
2 FORM jpeg
2 TITL Inline portrait
2 TYPE photo
1 OBJE
2 FILE docs/birth_cert.pdf
2 FORM pdf
0 @I2@ INDI
1 NAME Mary /Smith/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 OBJE
2 FILE photos/wedding_inline.jpg
2 FORM jpeg
2 TITL Wedding 1923
0 TRLR
"""


@pytest.mark.asyncio
async def test_import_persists_inline_obje_objects(postgres_dsn) -> None:
    """Inline OBJE (без xref) — сохраняются в multimedia_objects + entity_multimedia."""
    from shared_models.orm import EntityMultimedia, Family, MultimediaObject, Person
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, stats = await _run_import_via_runner(postgres_dsn, _INLINE_OBJE_GED, "inline.ged")
    assert stats["multimedia"] == 3
    assert stats["entity_multimedia"] == 3

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            objects = (
                (
                    await session.execute(
                        select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(objects) == 3
            assert all(o.object_metadata.get("inline") is True for o in objects)
            captions = {o.caption for o in objects}
            assert "Inline portrait" in captions
            assert "Wedding 1923" in captions
            assert None in captions
            i1_objects = [o for o in objects if o.object_metadata.get("inline_owner_xref") == "I1"]
            f1_objects = [o for o in objects if o.object_metadata.get("inline_owner_xref") == "F1"]
            assert len(i1_objects) == 2
            assert len(f1_objects) == 1
            portrait = next(o for o in i1_objects if o.caption == "Inline portrait")
            assert portrait.object_metadata.get("type") == "photo"

            i1 = (
                await session.execute(
                    select(Person).where(Person.tree_id == tree_id, Person.gedcom_xref == "I1")
                )
            ).scalar_one()
            f1 = (
                await session.execute(
                    select(Family).where(Family.tree_id == tree_id, Family.gedcom_xref == "F1")
                )
            ).scalar_one()
            person_links = (
                (
                    await session.execute(
                        select(EntityMultimedia).where(
                            EntityMultimedia.entity_type == "person",
                            EntityMultimedia.entity_id == i1.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            family_links = (
                (
                    await session.execute(
                        select(EntityMultimedia).where(
                            EntityMultimedia.entity_type == "family",
                            EntityMultimedia.entity_id == f1.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(person_links) == 2
            assert len(family_links) == 1
    finally:
        await engine.dispose()


_ANCESTRY_OBJE_GED = b"""\
0 HEAD
1 SOUR Ancestry.com
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 OBJE @M1@
0 @M1@ OBJE
1 FILE https://www.ancestry.com/img/abc.jpg
2 FORM jpeg
2 TYPE photo
1 TITL Ancestry photo
1 _CREA 2024-01-15 09:12:34
0 TRLR
"""


@pytest.mark.asyncio
async def test_import_captures_ancestry_crea_and_type_in_metadata(postgres_dsn) -> None:
    """Ancestry экспорт: _CREA → provenance.gedcom_crea, TYPE → metadata.type."""
    from shared_models.orm import MultimediaObject
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, _stats = await _run_import_via_runner(postgres_dsn, _ANCESTRY_OBJE_GED, "ancestry.ged")

    engine = create_async_engine(postgres_dsn, future=True)
    try:
        SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
        async with SessionMaker() as session:
            obj = (
                await session.execute(
                    select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
                )
            ).scalar_one()
            assert obj.object_metadata.get("type") == "photo"
            assert obj.object_metadata.get("format") == "jpeg"
            assert obj.object_metadata.get("created_raw") == "2024-01-15 09:12:34"
            assert obj.provenance.get("gedcom_crea") == "2024-01-15 09:12:34"
    finally:
        await engine.dispose()
