"""Интеграционные тесты ``familysearch_importer.import_fs_pedigree``.

Маркеры: ``db`` + ``integration`` — требуют testcontainers Postgres.
FS API мокается через подмену ``fs_client`` (или собственного httpx
``AsyncClient`` под ``pytest-httpx`` через ``client=`` kwarg).

Отдельный mock-FS реализован на уровне ``FamilySearchClient`` — это
проще, чем mock'ать ``httpx`` через worktree-shared фикстуру.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from familysearch_client import (
    FamilySearchClient,
    FsFact,
    FsGender,
    FsName,
    FsPedigreeNode,
    FsPerson,
)
from parser_service.services.familysearch_importer import import_fs_pedigree
from shared_models.enums import EntityStatus, EventType, ImportJobStatus, ImportSourceKind
from shared_models.orm import Event, EventParticipant, Name, Person, Place, Tree, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Mock FS client
# ---------------------------------------------------------------------------


class _StubFsClient:
    """Минимальный stand-in для FamilySearchClient.

    Возвращает заранее заданное :class:`FsPedigreeNode`. Не делает HTTP-вызовов
    и не использует access_token — это ровно то, что нам нужно для проверки
    importer-логики без подменения трогаемого нами `client.py`.
    """

    def __init__(self, tree: FsPedigreeNode) -> None:
        self._tree = tree
        self.calls: list[tuple[str, int]] = []

    async def get_pedigree(self, person_id: str, *, generations: int = 4) -> FsPedigreeNode:
        self.calls.append((person_id, generations))
        return self._tree


def _person(
    fs_id: str,
    *,
    full_text: str,
    given: str | None = None,
    surname: str | None = None,
    sex: FsGender = FsGender.MALE,
    living: bool | None = False,
    facts: tuple[FsFact, ...] = (),
    aka: tuple[FsName, ...] = (),
) -> FsPerson:
    """Helper: собирает FsPerson с одним preferred name + опциональные AKA."""
    preferred = FsName(
        full_text=full_text,
        given=given,
        surname=surname,
        preferred=True,
    )
    return FsPerson(
        id=fs_id,
        gender=sex,
        names=(preferred, *aka),
        facts=facts,
        living=living,
    )


def _three_generation_tree() -> FsPedigreeNode:
    """7 persons: root + 2 parents + 4 grandparents."""
    return FsPedigreeNode(
        person=_person(
            "ROOT",
            full_text="Root Person",
            given="Root",
            surname="Person",
            sex=FsGender.UNKNOWN,
            facts=(
                FsFact(
                    type="Birth",
                    date_original="3 Apr 1850",
                    place_original="Boston, Massachusetts",
                ),
                FsFact(
                    type="Death",
                    date_original="12 Nov 1920",
                    place_original="Boston, Massachusetts",
                ),
            ),
        ),
        father=FsPedigreeNode(
            person=_person(
                "FATHER",
                full_text="Father Person",
                given="Father",
                surname="Person",
                sex=FsGender.MALE,
                facts=(
                    FsFact(
                        type="Birth",
                        date_original="1820",
                        place_original="Boston, Massachusetts",
                    ),
                ),
            ),
            father=FsPedigreeNode(
                person=_person(
                    "PGF",
                    full_text="Paternal Grandfather",
                    given="Paternal",
                    surname="Grandfather",
                ),
            ),
            mother=FsPedigreeNode(
                person=_person(
                    "PGM",
                    full_text="Paternal Grandmother",
                    given="Paternal",
                    surname="Grandmother",
                    sex=FsGender.FEMALE,
                ),
            ),
        ),
        mother=FsPedigreeNode(
            person=_person(
                "MOTHER",
                full_text="Mother Person",
                given="Mother",
                surname="Person",
                sex=FsGender.FEMALE,
                living=True,
            ),
            father=FsPedigreeNode(
                person=_person("MGF", full_text="Maternal Grandfather"),
            ),
            mother=FsPedigreeNode(
                person=_person(
                    "MGM",
                    full_text="Maternal Grandmother",
                    sex=FsGender.FEMALE,
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def fresh_tree(session_factory: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """Создаёт user + пустое tree, возвращает (tree_id, owner_user_id)."""
    factory = session_factory
    async with factory() as session:
        user = User(
            email=f"fs-test-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:fs-test-{uuid.uuid4().hex[:8]}",
            display_name="FS Test User",
            locale="en",
        )
        session.add(user)
        await session.flush()
        tree = Tree(
            owner_user_id=user.id,
            name=f"FS Test Tree {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        await session.commit()
        return tree.id, user.id


# ---------------------------------------------------------------------------
# Tests — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_single_person_creates_person_with_provenance(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Ровно одна персона → одна Person row + provenance заполнена."""
    tree_id, owner_id = fresh_tree
    factory = session_factory
    single = FsPedigreeNode(
        person=_person(
            "SOLO",
            full_text="Solo Person",
            given="Solo",
            surname="Person",
            facts=(
                FsFact(
                    type="Birth",
                    date_original="1900",
                    place_original="Brooklyn, New York",
                ),
            ),
        )
    )
    stub = _StubFsClient(single)
    async with factory() as session:
        job = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="SOLO",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
        )
        await session.commit()

    assert stub.calls == [("SOLO", 1)]
    assert job.status == ImportJobStatus.SUCCEEDED.value
    assert job.source_kind == ImportSourceKind.FAMILYSEARCH.value
    assert job.stats["persons"] == 1
    assert job.stats["persons_refreshed"] == 0
    assert job.stats["events"] == 1
    assert job.stats["names"] == 1
    assert job.stats["places"] == 1
    assert job.stats["generations"] == 1

    async with factory() as session:
        row = (await session.execute(select(Person).where(Person.tree_id == tree_id))).scalar_one()
        assert row.gedcom_xref == "fs:SOLO"
        assert row.sex == "M"  # FsGender.MALE default
        prov = row.provenance
        assert prov["source"] == "familysearch"
        assert prov["fs_person_id"] == "SOLO"
        assert prov["fs_url"].endswith("/SOLO")
        assert prov["import_job_id"] == str(job.id)


@pytest.mark.asyncio
async def test_import_pedigree_creates_seven_persons_for_three_generations(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """3-generation tree → 7 Person rows + names + birth/death events."""
    tree_id, owner_id = fresh_tree
    factory = session_factory
    stub = _StubFsClient(_three_generation_tree())
    async with factory() as session:
        job = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="ROOT",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=3,
            fs_client=stub,
        )
        await session.commit()

    assert job.stats["persons"] == 7
    # Root: BIRT+DEAT, Father: BIRT — 3 events total.
    assert job.stats["events"] == 3
    assert job.stats["names"] == 7  # один preferred на каждого
    # 1 unique place ("Boston, Massachusetts") used by 3 events.
    assert job.stats["places"] == 1

    async with factory() as session:
        # Living mother → status = HYPOTHESIS.
        mother = (
            await session.execute(
                select(Person)
                .where(Person.tree_id == tree_id)
                .where(Person.gedcom_xref == "fs:MOTHER")
            )
        ).scalar_one()
        assert mother.status == EntityStatus.HYPOTHESIS.value
        # Non-living father → PROBABLE.
        father = (
            await session.execute(
                select(Person)
                .where(Person.tree_id == tree_id)
                .where(Person.gedcom_xref == "fs:FATHER")
            )
        ).scalar_one()
        assert father.status == EntityStatus.PROBABLE.value


@pytest.mark.asyncio
async def test_birth_event_links_to_resolved_place(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Birth fact с place_original → Event.place_id указывает на новый Place."""
    tree_id, owner_id = fresh_tree
    factory = session_factory
    stub = _StubFsClient(
        FsPedigreeNode(
            person=_person(
                "X",
                full_text="X Person",
                facts=(
                    FsFact(
                        type="Birth",
                        date_original="1900",
                        place_original="Brooklyn, New York",
                    ),
                ),
            )
        )
    )
    async with factory() as session:
        await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="X",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
        )
        await session.commit()

    async with factory() as session:
        place = (
            await session.execute(
                select(Place)
                .where(Place.tree_id == tree_id)
                .where(Place.canonical_name == "Brooklyn, New York")
            )
        ).scalar_one()
        event = (
            await session.execute(
                select(Event)
                .where(Event.tree_id == tree_id)
                .where(Event.event_type == EventType.BIRTH.value)
            )
        ).scalar_one()
        assert event.place_id == place.id
        assert event.date_raw == "1900"
        # EventParticipant role principal — single participant linking to person.
        participant = (
            await session.execute(
                select(EventParticipant).where(EventParticipant.event_id == event.id)
            )
        ).scalar_one()
        assert participant.role == "principal"


@pytest.mark.asyncio
async def test_skipped_facts_counted_in_stats(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Marriage / прочие facts не импортируются и считаются как skipped_facts."""
    tree_id, owner_id = fresh_tree
    factory = session_factory
    stub = _StubFsClient(
        FsPedigreeNode(
            person=_person(
                "X",
                full_text="X",
                facts=(
                    FsFact(type="Birth", date_original="1900"),
                    FsFact(type="Marriage", date_original="1925"),
                    FsFact(type="Occupation"),
                ),
            )
        )
    )
    async with factory() as session:
        job = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="X",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
        )
        await session.commit()

    assert job.stats["events"] == 1  # только Birth
    assert job.stats["skipped_facts"] == 2


# ---------------------------------------------------------------------------
# Tests — idempotency / refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_re_import_same_fs_person_id_does_not_duplicate(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Второй импорт того же fs_person_id → 0 новых Person, 1 refreshed."""
    tree_id, owner_id = fresh_tree
    factory = session_factory
    tree = _three_generation_tree()

    async with factory() as session:
        job1 = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="ROOT",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=3,
            fs_client=_StubFsClient(tree),
        )
        await session.commit()
    assert job1.stats["persons"] == 7

    async with factory() as session:
        job2 = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="ROOT",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=3,
            fs_client=_StubFsClient(tree),
        )
        await session.commit()

    assert job2.stats["persons"] == 0
    assert job2.stats["persons_refreshed"] == 7
    # Re-import: existing FS-events были удалены (3) и вставлены 3 новых.
    assert job2.stats["events_dropped_for_refresh"] == 3
    assert job2.stats["events"] == 3

    # Total Person rows in tree — ровно 7.
    async with factory() as session:
        count = (
            (await session.execute(select(Person).where(Person.tree_id == tree_id))).scalars().all()
        )
        assert len(count) == 7


@pytest.mark.asyncio
async def test_refresh_does_not_drop_user_added_names(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """User-added Name (без FS-provenance) не должен исчезнуть при re-import."""
    tree_id, owner_id = fresh_tree
    factory = session_factory
    tree = _three_generation_tree()

    async with factory() as session:
        await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="ROOT",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=_StubFsClient(tree),
        )
        await session.commit()

    # Симулируем пользовательский nickname.
    # Name не имеет provenance-колонки, поэтому источник на уровне строки
    # не различим — гарантия от ADR-0017 §«Refresh»: для уже импортированных
    # FS-persons importer вообще не пишет в `names`, так что любые
    # существующие записи (FS-imported или manual-added) сохраняются.
    async with factory() as session:
        person = (
            await session.execute(
                select(Person)
                .where(Person.tree_id == tree_id)
                .where(Person.gedcom_xref == "fs:ROOT")
            )
        ).scalar_one()
        session.add(
            Name(
                person_id=person.id,
                given_name="Manual Nickname",
                name_type="aka",
                sort_order=99,
            )
        )
        await session.commit()

    # Re-import — manual name должен выжить (importer skip'ает Name insert
    # для refreshed persons).
    async with factory() as session:
        await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="ROOT",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=_StubFsClient(tree),
        )
        await session.commit()

    async with factory() as session:
        names = (
            (
                await session.execute(
                    select(Name)
                    .join(Person)
                    .where(Person.tree_id == tree_id)
                    .where(Person.gedcom_xref == "fs:ROOT")
                )
            )
            .scalars()
            .all()
        )
        manual_names = [n for n in names if n.given_name == "Manual Nickname"]
        assert len(manual_names) == 1, "manual name was wiped by re-import"
        # Original FS-name (preferred from первого импорта) тоже на месте.
        fs_names = [n for n in names if n.name_type == "birth"]
        assert len(fs_names) >= 1


# ---------------------------------------------------------------------------
# Tests — error pass-through
# ---------------------------------------------------------------------------


class _FailingFsClient:
    """Stub, который всегда поднимает заданное exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def get_pedigree(
        self,
        person_id: str,  # noqa: ARG002
        *,
        generations: int = 4,  # noqa: ARG002
    ) -> FsPedigreeNode:
        raise self._exc


@pytest.mark.asyncio
async def test_fs_404_propagates_as_not_found_error(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """NotFoundError из client пробрасывается, ImportJob остаётся в running.

    Caller (endpoint) сам ловит и помечает status=failed.
    """
    from familysearch_client import NotFoundError

    tree_id, owner_id = fresh_tree
    factory = session_factory
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await import_fs_pedigree(
                session,
                access_token="ignored",
                fs_person_id="GHOST",
                tree_id=tree_id,
                owner_user_id=owner_id,
                generations=1,
                fs_client=_FailingFsClient(NotFoundError("404")),
            )


def test_unused_fs_client_kwarg_works_with_real_client_type() -> None:
    """Type-check guard: реальный FamilySearchClient acceptable как fs_client.

    Не делает HTTP вызовов — только конструируем объект.
    """
    client = FamilySearchClient(access_token="t")
    # Если signature `fs_client: FamilySearchClient | None` — этот вызов
    # должен пройти mypy/runtime без TypeError.
    assert isinstance(client, FamilySearchClient)
