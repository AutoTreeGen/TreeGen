"""Интеграционные тесты для FS dedup attempts (Phase 5.2.1).

Проверяет:

* Happy-path: import_fs_pedigree находит локального дубликата → 1 active
  ``FsDedupAttempt`` row.
* Idempotency на active: повторный import того же fs_pid не создаёт
  дубль attempt'а пока active.
* Idempotency после merge: ``merged_at`` установлен → re-import
  игнорирует кандидата.
* Cooldown: rejected на 31 день → re-import skip; rejected на 91 день →
  новый attempt.
* Direction stability: ``(A=fs, B=local)`` и ``(B=fs, A=local)`` —
  разные attempts, partial unique индекс не блокирует.
* Active-pair partial unique: два одновременных active attempt на ту же
  направленную пару → второй падает с IntegrityError.

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
from shared_models.enums import EntityStatus, EventType, NameType, Sex
from shared_models.orm import (
    Event,
    EventParticipant,
    FsDedupAttempt,
    Name,
    Person,
    Tree,
    User,
)
from shared_models.types import new_uuid
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Stubs / helpers
# ---------------------------------------------------------------------------


class _StubFsClient:
    """Минимальный stand-in для FamilySearchClient (та же логика, что в test_familysearch_importer)."""

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
            email=f"fsdedup-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:fsdedup-{uuid.uuid4().hex[:8]}",
            display_name="FS Dedup Test User",
            locale="en",
        )
        session.add(user)
        await session.flush()
        tree = Tree(
            owner_user_id=user.id,
            name=f"FS Dedup Test {uuid.uuid4().hex[:6]}",
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
) -> uuid.UUID:
    """Insert a non-FS Person + Name + optional BIRT event for matching.

    Person flush'ится отдельно от Name/Event'ов, потому что у этих ORM-
    моделей нет relationship() между собой — UOW не может гарантировать
    топологический порядок INSERT'ов, и FK-zависимости падают.
    """
    pid = new_uuid()
    session.add(
        Person(
            id=pid,
            tree_id=tree_id,
            gedcom_xref=None,
            sex=Sex.UNKNOWN.value,
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
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_import_creates_active_attempt_for_local_duplicate(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """FS-import видит локального дубликата → 1 active attempt."""
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
        )
        await session.commit()

    assert job.stats["fs_dedup_attempts_created"] == 1

    async with factory() as session:
        rows = (
            (await session.execute(select(FsDedupAttempt).where(FsDedupAttempt.tree_id == tree_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.candidate_person_id == local_id
        assert row.fs_pid == "FS-IVAN"
        assert row.reason == "fs_import_match"
        assert row.score >= 0.6
        assert row.rejected_at is None
        assert row.merged_at is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reimport_same_fs_pid_does_not_duplicate_active_attempt(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Повторный import того же fs_pid → 0 новых attempts."""
    tree_id, owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        await session.commit()

    stub = _StubFsClient(_fs_node("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850))

    # Первый import.
    async with factory() as session:
        job1 = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="FS-IVAN",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
        )
        await session.commit()
    assert job1.stats["fs_dedup_attempts_created"] == 1

    # Второй import — refreshed, не new → 0 кандидатов вообще.
    async with factory() as session:
        job2 = await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="FS-IVAN",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
        )
        await session.commit()
    assert job2.stats["fs_dedup_attempts_created"] == 0

    async with factory() as session:
        count = (
            (await session.execute(select(FsDedupAttempt).where(FsDedupAttempt.tree_id == tree_id)))
            .scalars()
            .all()
        )
        assert len(count) == 1


@pytest.mark.asyncio
async def test_merged_fs_pid_blocks_new_attempt_even_on_fresh_local_match(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """fs_pid с merged_at → re-import не создаёт новый attempt даже если местный матч свежий.

    Сценарий: FS-person ассимилирован (merged_at установлен). Появляется
    *другой* местный кандидат, который тоже подходит под этот FS-person.
    Так как fs_pid уже merged, importer не должен предлагать кандидата
    повторно (он мог быть сам же merged'нутым survivor'ом).
    """
    tree_id, owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        local_first = await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        await session.commit()

    stub = _StubFsClient(_fs_node("FS-IVAN", given="Ivan", surname="Ivanov", birth_year=1850))

    async with factory() as session:
        await import_fs_pedigree(
            session,
            access_token="ignored",
            fs_person_id="FS-IVAN",
            tree_id=tree_id,
            owner_user_id=owner_id,
            generations=1,
            fs_client=stub,
        )
        await session.commit()

    # Mark attempt as merged_at.
    async with factory() as session:
        att = (
            await session.execute(select(FsDedupAttempt).where(FsDedupAttempt.tree_id == tree_id))
        ).scalar_one()
        att.merged_at = dt.datetime.now(dt.UTC)
        await session.commit()

    # Add a SECOND local candidate (would otherwise score high) and re-run
    # discovery via a fresh fs_person_id collision is impossible (fs_pid
    # idempotency keys on string id). Instead, делаем re-import под новым
    # fs_pid'ом — НО для проверки именно «merged blocks», переиспользуем
    # тот же fs_pid: после refresh новых FS-persons не появится, но это
    # как раз и есть «idempotency через refresh path». Проверим иначе:
    # удаляем local Person → re-import создаёт новый FS person row → но
    # т.к. он refreshed, он не new. Тоже не подходит.
    #
    # Чистый способ: вручную дёргаем helper персистенс'а на новом FS person,
    # с тем же fs_pid. Это симулирует «другая FS-person с тем же external
    # id, или повторный сценарий», и проверяет ровно фильтр merged_fs_pids.
    from parser_service.services.familysearch_importer import _persist_fs_dedup_attempts

    # Создаём новую FS-имитированную person row + новую local-кандидатку.
    async with factory() as session:
        local_second = await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        new_fs_id = new_uuid()
        session.add(
            Person(
                id=new_fs_id,
                tree_id=tree_id,
                gedcom_xref="fs:FS-IVAN-DUP",
                sex=Sex.UNKNOWN.value,
                status=EntityStatus.PROBABLE.value,
                confidence_score=0.5,
                version_id=1,
                provenance={"source": "familysearch", "fs_person_id": "FS-IVAN"},
            )
        )
        await session.flush()
        session.add(
            Name(
                id=new_uuid(),
                person_id=new_fs_id,
                given_name="Ivan",
                surname="Ivanov",
                sort_order=0,
                name_type=NameType.BIRTH.value,
            )
        )
        await session.flush()
        # Сейчас fs_pid 'FS-IVAN' имеет merged-attempt → helper пропустит.
        created = await _persist_fs_dedup_attempts(
            session,
            tree_id=tree_id,
            new_fs_person_ids=[new_fs_id],
            job_id=new_uuid(),
            now=dt.datetime.now(dt.UTC),
        )
        await session.commit()

    assert created == 0, f"merged fs_pid should block new attempts, got {created}"
    # local_second должен оставаться без attempt'а.
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(FsDedupAttempt).where(FsDedupAttempt.candidate_person_id == local_second)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 0
    _ = local_first  # silence ARG; используется в attempt-row выше


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_reject_blocks_reimport_but_old_reject_allows_new_attempt(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Cooldown 90 дней: rejected_at на 31 день → skip. На 91 день → новый attempt."""
    tree_id, owner_id = fresh_tree
    factory = session_factory

    from parser_service.services.familysearch_importer import _persist_fs_dedup_attempts

    async with factory() as session:
        local_id = await _create_local_person(
            session, tree_id, given="Ivan", surname="Ivanov", birth_year=1850
        )
        # FS-side person row, как если бы её уже импортировали.
        fs_id = new_uuid()
        session.add(
            Person(
                id=fs_id,
                tree_id=tree_id,
                gedcom_xref="fs:FS-IVAN",
                sex=Sex.UNKNOWN.value,
                status=EntityStatus.PROBABLE.value,
                confidence_score=0.5,
                version_id=1,
                provenance={"source": "familysearch", "fs_person_id": "FS-IVAN"},
            )
        )
        await session.flush()
        session.add(
            Name(
                id=new_uuid(),
                person_id=fs_id,
                given_name="Ivan",
                surname="Ivanov",
                sort_order=0,
                name_type=NameType.BIRTH.value,
            )
        )
        await session.flush()
        # Reject-row 31 день назад.
        rejected_recent = dt.datetime.now(dt.UTC) - dt.timedelta(days=31)
        session.add(
            FsDedupAttempt(
                id=new_uuid(),
                tree_id=tree_id,
                fs_person_id=fs_id,
                candidate_person_id=local_id,
                score=0.95,
                reason="fs_import_match",
                fs_pid="FS-IVAN",
                rejected_at=rejected_recent,
                provenance={},
            )
        )
        await session.commit()

    # Re-run персистенс при «текущем» now → cooldown активен.
    async with factory() as session:
        created = await _persist_fs_dedup_attempts(
            session,
            tree_id=tree_id,
            new_fs_person_ids=[fs_id],
            job_id=new_uuid(),
            now=dt.datetime.now(dt.UTC),
        )
        await session.commit()
    assert created == 0, "31-day-old reject must block re-suggestion"

    # Сдвигаем reject_at в прошлое на 91 день. Фильтруем по tree_id —
    # параллельные тесты могут переиспользовать общий postgres_dsn и
    # независимо вставлять fs_pid='FS-IVAN' в свои деревья.
    async with factory() as session:
        att = (
            await session.execute(
                select(FsDedupAttempt).where(
                    FsDedupAttempt.tree_id == tree_id,
                    FsDedupAttempt.fs_pid == "FS-IVAN",
                )
            )
        ).scalar_one()
        att.rejected_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=91)
        await session.commit()

    async with factory() as session:
        created = await _persist_fs_dedup_attempts(
            session,
            tree_id=tree_id,
            new_fs_person_ids=[fs_id],
            job_id=new_uuid(),
            now=dt.datetime.now(dt.UTC),
        )
        await session.commit()
    assert created == 1, "reject older than 90 days must allow new attempt"

    _ = owner_id  # ARG silence


# ---------------------------------------------------------------------------
# Direction stability — partial unique index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reverse_direction_pair_does_not_collide(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """(A→B) и (B→A) — разные attempts; partial unique не должен мешать."""
    tree_id, _owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        a_id = await _create_local_person(session, tree_id, given="Aa", surname="A")
        b_id = await _create_local_person(session, tree_id, given="Bb", surname="B")
        session.add(
            FsDedupAttempt(
                id=new_uuid(),
                tree_id=tree_id,
                fs_person_id=a_id,
                candidate_person_id=b_id,
                score=0.8,
                reason="fs_import_match",
                fs_pid=None,
                provenance={},
            )
        )
        session.add(
            FsDedupAttempt(
                id=new_uuid(),
                tree_id=tree_id,
                fs_person_id=b_id,
                candidate_person_id=a_id,
                score=0.8,
                reason="fs_import_match",
                fs_pid=None,
                provenance={},
            )
        )
        await session.commit()

    async with factory() as session:
        rows = (
            (await session.execute(select(FsDedupAttempt).where(FsDedupAttempt.tree_id == tree_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Active-pair partial unique constraint (DB-level safety net)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_active_attempts_on_same_directional_pair_violates_unique(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Партиал-уникальный индекс блокирует второй active attempt на ту же пару."""
    tree_id, _owner_id = fresh_tree
    factory = session_factory

    async with factory() as session:
        a_id = await _create_local_person(session, tree_id, given="A1", surname="X")
        b_id = await _create_local_person(session, tree_id, given="B2", surname="Y")
        session.add(
            FsDedupAttempt(
                id=new_uuid(),
                tree_id=tree_id,
                fs_person_id=a_id,
                candidate_person_id=b_id,
                score=0.8,
                reason="fs_import_match",
                fs_pid=None,
                provenance={},
            )
        )
        await session.commit()

    async with factory() as session:
        session.add(
            FsDedupAttempt(
                id=new_uuid(),
                tree_id=tree_id,
                fs_person_id=a_id,
                candidate_person_id=b_id,
                score=0.85,
                reason="fs_import_match",
                fs_pid=None,
                provenance={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    # Если первый attempt rejected — второй active должен пройти.
    async with factory() as session:
        first = (
            await session.execute(
                select(FsDedupAttempt).where(
                    FsDedupAttempt.tree_id == tree_id,
                    FsDedupAttempt.fs_person_id == a_id,
                )
            )
        ).scalar_one()
        first.rejected_at = dt.datetime.now(dt.UTC)
        await session.commit()

    async with factory() as session:
        session.add(
            FsDedupAttempt(
                id=new_uuid(),
                tree_id=tree_id,
                fs_person_id=a_id,
                candidate_person_id=b_id,
                score=0.85,
                reason="fs_import_match",
                fs_pid=None,
                provenance={},
            )
        )
        await session.commit()

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(FsDedupAttempt).where(
                        FsDedupAttempt.tree_id == tree_id,
                        FsDedupAttempt.fs_person_id == a_id,
                        FsDedupAttempt.candidate_person_id == b_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
