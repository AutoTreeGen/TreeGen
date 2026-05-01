"""Shared fixtures для tests/court_ready.

Фабрики копируют стиль ``tests/test_relationships_evidence.py`` —
direct ORM seeding через async_sessionmaker, отдельный для каждой
unit-фабрики чтобы не зависать на shared session-state.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import (
    Citation,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Name,
    Person,
    Place,
    Source,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"cr-{uuid.uuid4().hex[:8]}@example.com"
    async with factory() as session:
        user = User(
            email=e,
            external_auth_id=f"local:{e}",
            display_name="Test Researcher",
            locale="en",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def make_tree(factory: Any, *, owner: User, name: str = "Court Tree") -> Tree:
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=name,
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        m = TreeMembership(
            tree_id=tree.id,
            user_id=owner.id,
            role=TreeRole.OWNER.value,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(m)
        await session.commit()
        await session.refresh(tree)
        return tree


async def make_person(
    factory: Any,
    *,
    tree: Tree,
    given: str = "Sigmund",
    surname: str = "Levitin",
    sex: str = "M",
) -> Person:
    async with factory() as session:
        person = Person(tree_id=tree.id, sex=sex)
        session.add(person)
        await session.flush()
        name = Name(
            person_id=person.id,
            given_name=given,
            surname=surname,
            sort_order=0,
        )
        session.add(name)
        await session.commit()
        await session.refresh(person, attribute_names=["names"])
        return person


async def make_place(factory: Any, *, tree: Tree, name: str) -> Place:
    async with factory() as session:
        place = Place(tree_id=tree.id, canonical_name=name, provenance={}, version_id=1)
        session.add(place)
        await session.commit()
        await session.refresh(place)
        return place


async def make_event(
    factory: Any,
    *,
    tree: Tree,
    person: Person,
    event_type: str,
    date_start: dt.date | None = None,
    date_raw: str | None = None,
    place: Place | None = None,
    description: str | None = None,
) -> Event:
    async with factory() as session:
        event = Event(
            tree_id=tree.id,
            event_type=event_type,
            date_raw=date_raw,
            date_start=date_start,
            place_id=place.id if place else None,
            description=description,
            provenance={},
            version_id=1,
        )
        session.add(event)
        await session.flush()
        ep = EventParticipant(event_id=event.id, person_id=person.id, role="principal")
        session.add(ep)
        await session.commit()
        await session.refresh(event)
        return event


async def make_family(
    factory: Any,
    *,
    tree: Tree,
    husband: Person | None = None,
    wife: Person | None = None,
) -> Family:
    async with factory() as session:
        family = Family(
            tree_id=tree.id,
            husband_id=husband.id if husband else None,
            wife_id=wife.id if wife else None,
            provenance={},
            version_id=1,
        )
        session.add(family)
        await session.commit()
        await session.refresh(family)
        return family


async def add_child(factory: Any, *, family: Family, child: Person) -> None:
    async with factory() as session:
        fc = FamilyChild(family_id=family.id, child_person_id=child.id)
        session.add(fc)
        await session.commit()


async def make_source_and_citation(
    factory: Any,
    *,
    tree: Tree,
    entity_type: str,
    entity_id: uuid.UUID,
    title: str = "Test source",
    author: str | None = None,
    repository: str | None = None,
    page: str | None = None,
    snippet: str | None = None,
    quality: float = 0.7,
    quay_raw: int | None = None,
) -> tuple[Source, Citation]:
    async with factory() as session:
        source = Source(
            tree_id=tree.id,
            title=title,
            author=author,
            repository=repository,
            provenance={},
            version_id=1,
        )
        session.add(source)
        await session.flush()
        citation = Citation(
            tree_id=tree.id,
            source_id=source.id,
            entity_type=entity_type,
            entity_id=entity_id,
            page_or_section=page,
            quoted_text=snippet,
            quality=quality,
            quay_raw=quay_raw,
            provenance={},
        )
        session.add(citation)
        await session.commit()
        await session.refresh(source)
        await session.refresh(citation)
        return source, citation


def hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}
