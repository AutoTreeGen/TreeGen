"""Интеграционные тесты для Phase 5.2 fs_pedigree_merger.

Покрывает:

* :func:`resolve_fs_person` решает SKIP/MERGE при high-confidence матче
  по entity-resolution scorer'у.
* :func:`resolve_fs_person` решает CREATE_AS_NEW (с/без needs_review-flag'а)
  по low/mid-confidence коридорам.
* End-to-end FS import с merge_strategy_resolver: при MERGE-decision'е
  Person не создаётся как новый, а existing local'у прицепляются
  Names/Events с FS-provenance, и FS-attachment попадает в provenance.
* Audit-лог ``fs_import_merge_attempts`` содержит по одной row на каждое
  merge-decision'е импорта.

Все тесты — ``db`` + ``integration`` (testcontainers Postgres).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from familysearch_client import (
    FsFact,
    FsGender,
    FsName,
    FsPedigreeNode,
    FsPerson,
)
from parser_service.services.familysearch_importer import import_fs_pedigree
from parser_service.services.fs_pedigree_merger import (
    HIGH_CONFIDENCE_THRESHOLD,
    MID_CONFIDENCE_THRESHOLD,
    resolve_fs_person,
)
from shared_models.enums import (
    EntityStatus,
    EventType,
    MergeStrategy,
    NameType,
    Sex,
)
from shared_models.orm import (
    Event,
    EventParticipant,
    FsImportMergeAttempt,
    Name,
    Person,
    Tree,
    User,
)
from shared_models.types import new_uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Stubs / helpers
# ---------------------------------------------------------------------------


class _StubFsClient:
    """Минимальный stand-in для FamilySearchClient (та же форма что в test_familysearch_importer)."""

    def __init__(self, tree: FsPedigreeNode) -> None:
        self._tree = tree

    async def get_pedigree(self, person_id: str, *, generations: int = 4) -> FsPedigreeNode:  # noqa: ARG002
        return self._tree


def _fs_person(
    fs_id: str,
    *,
    given: str,
    surname: str,
    birth_year: int | None = None,
    sex: FsGender = FsGender.UNKNOWN,
) -> FsPerson:
    facts: tuple[FsFact, ...] = ()
    if birth_year is not None:
        facts = (FsFact(type="Birth", date_original=str(birth_year)),)
    return FsPerson(
        id=fs_id,
        gender=sex,
        names=(
            FsName(
                full_text=f"{given} {surname}",
                given=given,
                surname=surname,
                preferred=True,
            ),
        ),
        facts=facts,
    )


def _fs_node(
    fs_id: str, *, given: str, surname: str, birth_year: int | None = None
) -> FsPedigreeNode:
    return FsPedigreeNode(
        person=_fs_person(fs_id, given=given, surname=surname, birth_year=birth_year)
    )


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def fresh_tree(session_factory: Any) -> tuple[uuid.UUID, uuid.UUID]:
    factory = session_factory
    async with factory() as session:
        user = User(
            email=f"fsmerger-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:fsmerger-{uuid.uuid4().hex[:8]}",
            display_name="FS Merger Test User",
            locale="en",
        )
        session.add(user)
        await session.flush()
        tree = Tree(
            owner_user_id=user.id,
            name=f"FS Merger Test {uuid.uuid4().hex[:6]}",
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


async def _create_local_person(
    session: Any,
    tree_id: uuid.UUID,
    *,
    given: str,
    surname: str,
    birth_year: int | None = None,
    sex: str = Sex.UNKNOWN.value,
) -> uuid.UUID:
    """Insert a non-FS Person + Name + optional BIRT event (for matching)."""
    pid = new_uuid()
    session.add(
        Person(
            id=pid,
            tree_id=tree_id,
            gedcom_xref=None,
            sex=sex,
            status=EntityStatus.PROBABLE.value,
            confidence_score=0.5,
            version_id=1,
            provenance={},  # no source: 'familysearch'
        )
    )
    await session.flush()
    session.add(
        Name(
            id=new_uuid(),
            person_id=pid,
            given_name=given,
            surname=surname,
            sort_order=0,
            name_type=NameType.BIRTH.value,
        )
    )
    if birth_year is not None:
        ev_id = new_uuid()
        session.add(
            Event(
                id=ev_id,
                tree_id=tree_id,
                event_type=EventType.BIRTH.value,
                date_start=dt.date(birth_year, 1, 1),
                status=EntityStatus.PROBABLE.value,
                confidence_score=0.5,
                version_id=1,
                provenance={},
            )
        )
        await session.flush()
        session.add(
            EventParticipant(
                id=new_uuid(),
                event_id=ev_id,
                person_id=pid,
                family_id=None,
                role="principal",
            )
        )
    await session.flush()
    return pid


# ---------------------------------------------------------------------------
# resolve_fs_person — unit-style тесты (одна сессия, один tree)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_fs_person_high_confidence_skip(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """High-confidence match по name+birth_year → MERGE-стратегия с matched_person_id.

    Имя теста — «skip» в брифе как обобщённый ярлык high-confidence-decision'а
    (SKIP/MERGE — оба означают «не создавай дубликат»). resolver возвращает
    MERGE для нового fs_pid'а; SKIP резервируется для idempotent re-import'а.
    """
    tree_id, _owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        local_id = await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        await session.commit()

    fs = _fs_person("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850)

    async with factory() as session:
        result = await resolve_fs_person(session, fs, tree_id)

    assert result.strategy == MergeStrategy.MERGE
    assert result.matched_person_id == local_id
    assert result.score is not None
    assert result.score >= HIGH_CONFIDENCE_THRESHOLD
    assert result.needs_review is False
    assert result.reason == "high_confidence_match"


@pytest.mark.asyncio
async def test_resolve_fs_person_idempotent_skip_when_fs_pid_already_present(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Если fs_pid уже сматчен на Person в дереве → SKIP без скорера."""
    tree_id, _owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        existing_fs_id = new_uuid()
        session.add(
            Person(
                id=existing_fs_id,
                tree_id=tree_id,
                gedcom_xref="fs:FS-IVAN",
                sex=Sex.UNKNOWN.value,
                status=EntityStatus.PROBABLE.value,
                confidence_score=0.5,
                version_id=1,
                provenance={"source": "familysearch", "fs_person_id": "FS-IVAN"},
            )
        )
        await session.commit()

    fs = _fs_person("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850)

    async with factory() as session:
        result = await resolve_fs_person(session, fs, tree_id)

    assert result.strategy == MergeStrategy.SKIP
    assert result.matched_person_id == existing_fs_id
    assert result.reason == "fs_pid_idempotent"
    assert result.score is None


@pytest.mark.asyncio
async def test_resolve_fs_person_low_confidence_creates_new(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Имя-год не совпадают → CREATE_AS_NEW без needs_review."""
    tree_id, _owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        # Local: совершенно другое имя + год — score должен быть < mid_threshold.
        await _create_local_person(
            session, tree_id, given="Vasily", surname="Petrov", birth_year=1700
        )
        await session.commit()

    fs = _fs_person("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1900)

    async with factory() as session:
        result = await resolve_fs_person(session, fs, tree_id)

    assert result.strategy == MergeStrategy.CREATE_AS_NEW
    assert result.needs_review is False
    if result.score is not None:
        assert result.score < MID_CONFIDENCE_THRESHOLD


@pytest.mark.asyncio
async def test_resolve_fs_person_mid_confidence_flags_for_review(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Mid-confidence коридор → CREATE_AS_NEW + needs_review=True.

    Намеренно low high_threshold, чтобы вытащить mid-зону без подбора
    идеально-средне-совпадающих фамилий (зависит от scorer-весов).
    """
    tree_id, _owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        await session.commit()

    fs = _fs_person("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850)

    async with factory() as session:
        result = await resolve_fs_person(
            session,
            fs,
            tree_id,
            # Сдвигаем high-порог выше любого реального score'а scorer'а
            # (scorer max = 1.0 при идеально совпадающих компонентах).
            high_threshold=1.5,
            mid_threshold=0.5,
        )

    assert result.strategy == MergeStrategy.CREATE_AS_NEW
    assert result.needs_review is True
    assert result.matched_person_id is not None
    assert result.reason == "mid_confidence_review"


@pytest.mark.asyncio
async def test_resolve_fs_person_no_candidates_creates_new(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Пустое дерево → CREATE_AS_NEW, no_candidates."""
    tree_id, _owner_id = fresh_tree
    factory = session_factory

    fs = _fs_person("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850)

    async with factory() as session:
        result = await resolve_fs_person(session, fs, tree_id)

    assert result.strategy == MergeStrategy.CREATE_AS_NEW
    assert result.matched_person_id is None
    assert result.score is None
    assert result.needs_review is False
    assert result.reason == "no_candidates"


# ---------------------------------------------------------------------------
# End-to-end importer with merge_strategy_resolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_import_with_merge_strategy_appends_sources_to_existing_person(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """MERGE-decision: existing local Person получает FS-attachment + Name/Event под собой."""
    tree_id, owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        local_id = await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        await session.commit()

    stub = _StubFsClient(_fs_node("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850))

    async with factory() as session:
        job = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="FS-IVAN",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
            merge_strategy_resolver=resolve_fs_person,
        )
        await session.commit()

    # MERGE → новый Person row не создавался (persons=0), но names/events
    # импортируются под существующего Person'а.
    assert job.stats["persons"] == 0
    assert job.stats["fs_merge_merge"] == 1
    assert job.stats["fs_merge_skip"] == 0
    assert job.stats["fs_merge_create_as_new"] == 0
    # Один name (preferred → AKA в merge target'е) + один event (Birth)
    # должны прилететь под local_id.
    assert job.stats["names"] == 1
    assert job.stats["events"] == 1

    async with factory() as session:
        # Names под local_id получили AKA от FS.
        names = (
            (await session.execute(select(Name).where(Name.person_id == local_id))).scalars().all()
        )
        # 1 local primary + 1 FS AKA.
        assert len(names) == 2
        aka_names = [n for n in names if n.name_type == NameType.AKA.value]
        assert len(aka_names) == 1
        # Events под local_id получили FS BIRTH с FS provenance.
        events = (
            (
                await session.execute(
                    select(Event)
                    .join(EventParticipant, EventParticipant.event_id == Event.id)
                    .where(EventParticipant.person_id == local_id)
                )
            )
            .scalars()
            .all()
        )
        fs_events = [e for e in events if (e.provenance or {}).get("source") == "familysearch"]
        assert len(fs_events) == 1
        # Local Person'а provenance получает fs_attachments record.
        local = (await session.execute(select(Person).where(Person.id == local_id))).scalar_one()
        attachments = (local.provenance or {}).get("fs_attachments")
        assert isinstance(attachments, list)
        assert len(attachments) == 1
        assert attachments[0]["fs_pid"] == "FS-IVAN"


@pytest.mark.asyncio
async def test_merge_attempts_audit_log_records_each_decision(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Каждое FS-person decision → одна row в fs_import_merge_attempts.

    Multi-person pedigree с 3 FS-persons, 1 local-кандидатом:
    * FS-person #1 матчит local → MERGE.
    * FS-person #2 не имеет матча → CREATE_AS_NEW.
    * FS-person #3 не имеет матча → CREATE_AS_NEW.

    Ожидаем 3 attempt-row, по одной на каждое решение.
    """
    tree_id, owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        await session.commit()

    pedigree = FsPedigreeNode(
        person=_fs_person("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850),
        father=FsPedigreeNode(
            person=_fs_person("FS-FATHER", given="Pyotr", surname="Ivanov", birth_year=1820),
        ),
        mother=FsPedigreeNode(
            person=_fs_person("FS-MOTHER", given="Maria", surname="Sidorova", birth_year=1825),
        ),
    )
    stub = _StubFsClient(pedigree)

    async with factory() as session:
        job = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="FS-IVAN",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=2,
            fs_client=stub,
            merge_strategy_resolver=resolve_fs_person,
        )
        await session.commit()

    assert job.stats["fs_merge_attempts_total"] == 3
    assert job.stats["fs_merge_merge"] == 1
    assert job.stats["fs_merge_create_as_new"] == 2

    async with factory() as session:
        attempts = (
            (
                await session.execute(
                    select(FsImportMergeAttempt).where(FsImportMergeAttempt.tree_id == tree_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(attempts) == 3
        by_pid = {a.fs_pid: a for a in attempts}
        assert by_pid["FS-IVAN"].strategy == MergeStrategy.MERGE.value
        assert by_pid["FS-IVAN"].score is not None
        assert by_pid["FS-IVAN"].matched_person_id is not None
        assert by_pid["FS-FATHER"].strategy == MergeStrategy.CREATE_AS_NEW.value
        assert by_pid["FS-MOTHER"].strategy == MergeStrategy.CREATE_AS_NEW.value
        # Все три должны быть привязаны к одному import_job_id.
        assert {a.import_job_id for a in attempts} == {job.id}


@pytest.mark.asyncio
async def test_fs_import_without_resolver_keeps_phase_5_1_behaviour(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """merge_strategy_resolver=None → ноль merge-attempts, всегда CREATE_AS_NEW."""
    tree_id, owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        await session.commit()

    stub = _StubFsClient(_fs_node("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850))

    async with factory() as session:
        job = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="FS-IVAN",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
            # No resolver — Phase 5.1 baseline.
        )
        await session.commit()

    # Без merger'а — всегда создаём новый Person, attempts не пишем.
    assert job.stats["persons"] == 1
    assert job.stats["fs_merge_attempts_total"] == 0

    async with factory() as session:
        attempts = (
            (
                await session.execute(
                    select(FsImportMergeAttempt).where(FsImportMergeAttempt.tree_id == tree_id)
                )
            )
            .scalars()
            .all()
        )
        assert attempts == []
