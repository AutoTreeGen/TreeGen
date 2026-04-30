"""Интеграционные тесты ``wikimedia_importer.import_wikimedia_for_place``.

Маркеры: ``db`` + ``integration`` (требуют testcontainers Postgres).
Wikimedia API мокается через подмену ``commons_client`` — никаких
реальных HTTP-вызовов в этих тестах.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from parser_service.services.wikimedia_importer import (
    PlaceNotFoundError,
    import_wikimedia_for_place,
)
from shared_models.enums import EntityStatus
from shared_models.orm import EntityMultimedia, MultimediaObject, Place, Tree, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wikimedia_commons_client import (
    Attribution,
    CommonsImage,
    License,
)

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Stub Commons client — no HTTP, no UA-policy concerns.
# ---------------------------------------------------------------------------


class _StubCommonsClient:
    """Минимальный stand-in для :class:`WikimediaCommonsClient`.

    Записывает все вызовы для ассертов и возвращает заранее заданный
    список изображений. Не заходит в HTTP.
    """

    def __init__(self, images: list[CommonsImage]) -> None:
        self.images = images
        self.geo_calls: list[tuple[float, float, int, int]] = []
        self.text_calls: list[tuple[str, int]] = []

    async def search_by_coordinates(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_m: int,
        limit: int,
    ) -> list[CommonsImage]:
        self.geo_calls.append((latitude, longitude, radius_m, limit))
        return list(self.images)

    async def search_by_title(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[CommonsImage]:
        self.text_calls.append((query, limit))
        return list(self.images)


def _img(
    *,
    title: str = "File:Vilnius_synagogue.jpg",
    page_url: str = "https://commons.wikimedia.org/wiki/File:Vilnius_synagogue.jpg",
    image_url: str = "https://upload.wikimedia.org/wikipedia/commons/a/aa/Vilnius_synagogue.jpg",
    license_short: str | None = "CC BY-SA 4.0",
    credit: str | None = "<a href='https://example.org'>Photo by J. Doe</a>",
    attribution_required: bool = True,
) -> CommonsImage:
    license_obj = (
        License(short_name=license_short, url="https://creativecommons.org/licenses/by-sa/4.0")
        if license_short is not None
        else None
    )
    return CommonsImage(
        title=title,
        page_url=page_url,
        image_url=image_url,
        thumb_url="https://upload.wikimedia.org/thumb/Vilnius_synagogue.jpg",
        width=1024,
        height=768,
        mime="image/jpeg",
        license=license_obj,
        attribution=Attribution(credit_html=credit, required=attribution_required),
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
    async with session_factory() as session:
        user = User(
            email=f"wm-test-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:wm-test-{uuid.uuid4().hex[:8]}",
            display_name="Wikimedia Test User",
            locale="en",
        )
        session.add(user)
        await session.flush()
        tree = Tree(
            owner_user_id=user.id,
            name=f"WM Test Tree {uuid.uuid4().hex[:6]}",
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


async def _make_place(
    session_factory: Any,
    *,
    tree_id: uuid.UUID,
    canonical_name: str = "Vilnius",
    latitude: float | None = 54.687,
    longitude: float | None = 25.279,
) -> uuid.UUID:
    async with session_factory() as session:
        place = Place(
            tree_id=tree_id,
            canonical_name=canonical_name,
            latitude=latitude,
            longitude=longitude,
            status=EntityStatus.PROBABLE.value,
            confidence_score=0.5,
            version_id=1,
            provenance={},
        )
        session.add(place)
        await session.flush()
        await session.commit()
        return place.id


# ---------------------------------------------------------------------------
# Happy path — geosearch
# ---------------------------------------------------------------------------


async def test_geosearch_creates_multimedia_and_entity_link(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Place с координатами → geosearch + MultimediaObject + EntityMultimedia."""
    tree_id, _owner = fresh_tree
    place_id = await _make_place(session_factory, tree_id=tree_id)
    stub = _StubCommonsClient([_img()])

    async with session_factory() as session:
        stats = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="test-ua/1.0",
            commons_client=stub,
        )
        await session.commit()

    assert stats.fetched == 1
    assert stats.created == 1
    assert stats.skipped_existing == 0
    assert stats.search_strategy == "geosearch"
    # Geo вызов сделан, text — нет.
    assert len(stub.geo_calls) == 1
    assert stub.geo_calls[0][0] == pytest.approx(54.687)
    assert stub.geo_calls[0][1] == pytest.approx(25.279)
    assert stub.text_calls == []

    async with session_factory() as session:
        mm_rows = (
            (
                await session.execute(
                    select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(mm_rows) == 1
        mm = mm_rows[0]
        assert mm.object_type == "image"
        assert mm.storage_url.endswith("Vilnius_synagogue.jpg")
        assert mm.caption == "File:Vilnius_synagogue.jpg"
        # Provenance carries license + commons URL.
        assert mm.provenance["source"] == "wikimedia_commons"
        assert mm.provenance["commons_page_url"].endswith("File:Vilnius_synagogue.jpg")
        assert mm.provenance["license_short_name"] == "CC BY-SA 4.0"
        assert mm.provenance["attribution_required"] is True
        assert "fetched_at" in mm.provenance
        # Object metadata: thumb_url + credit.
        assert mm.object_metadata["thumb_url"].endswith(".jpg")
        assert "Photo by J. Doe" in mm.object_metadata["credit_html"]
        assert mm.object_metadata["width"] == 1024

        link_rows = (
            (
                await session.execute(
                    select(EntityMultimedia).where(EntityMultimedia.multimedia_id == mm.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(link_rows) == 1
        assert link_rows[0].entity_type == "place"
        assert link_rows[0].entity_id == place_id


# ---------------------------------------------------------------------------
# Text fallback
# ---------------------------------------------------------------------------


async def test_text_fallback_when_place_has_no_coordinates(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Place без lat/lon → search_by_title по canonical_name."""
    tree_id, _owner = fresh_tree
    place_id = await _make_place(
        session_factory,
        tree_id=tree_id,
        canonical_name="Łódź",
        latitude=None,
        longitude=None,
    )
    stub = _StubCommonsClient(
        [
            _img(
                title="File:Łódź_synagogue.jpg",
                page_url="https://commons.wikimedia.org/wiki/File:Lodz.jpg",
            )
        ]
    )

    async with session_factory() as session:
        stats = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="test-ua/1.0",
            commons_client=stub,
        )
        await session.commit()

    assert stats.search_strategy == "text"
    assert stub.geo_calls == []
    assert len(stub.text_calls) == 1
    assert stub.text_calls[0][0] == "Łódź"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_repeat_fetch_is_idempotent(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Повторный fetch с тем же page_url не дублирует MultimediaObject."""
    tree_id, _owner = fresh_tree
    place_id = await _make_place(session_factory, tree_id=tree_id)
    stub = _StubCommonsClient([_img()])

    async with session_factory() as session:
        first = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="ua",
            commons_client=stub,
        )
        await session.commit()

    async with session_factory() as session:
        second = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="ua",
            commons_client=stub,
        )
        await session.commit()

    assert first.created == 1
    assert second.created == 0
    assert second.skipped_existing == 1

    async with session_factory() as session:
        count = (
            (
                await session.execute(
                    select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 1


async def test_two_distinct_images_create_two_rows(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Два разных Commons-URL'а → две MultimediaObject rows + два EntityMultimedia link'а."""
    tree_id, _owner = fresh_tree
    place_id = await _make_place(session_factory, tree_id=tree_id)
    images = [
        _img(
            title="File:A.jpg",
            page_url="https://commons.wikimedia.org/wiki/File:A.jpg",
            image_url="https://upload.wikimedia.org/A.jpg",
        ),
        _img(
            title="File:B.jpg",
            page_url="https://commons.wikimedia.org/wiki/File:B.jpg",
            image_url="https://upload.wikimedia.org/B.jpg",
        ),
    ]
    stub = _StubCommonsClient(images)

    async with session_factory() as session:
        stats = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="ua",
            commons_client=stub,
        )
        await session.commit()

    assert stats.created == 2
    async with session_factory() as session:
        link_count = (
            (
                await session.execute(
                    select(EntityMultimedia).where(
                        EntityMultimedia.entity_id == place_id,
                        EntityMultimedia.entity_type == "place",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(link_count) == 2


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_place_not_found_raises(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tree_id, _owner = fresh_tree
    stub = _StubCommonsClient([_img()])

    async with session_factory() as session:
        with pytest.raises(PlaceNotFoundError):
            await import_wikimedia_for_place(
                session,
                tree_id=tree_id,
                place_id=uuid.uuid4(),  # non-existent
                user_agent="ua",
                commons_client=stub,
            )

    # Никаких HTTP-вызовов не должно было произойти.
    assert stub.geo_calls == []
    assert stub.text_calls == []


async def test_place_from_other_tree_treated_as_not_found(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Cross-tree isolation: Place из чужого дерева → PlaceNotFoundError."""
    tree_a, _owner = fresh_tree
    place_a = await _make_place(session_factory, tree_id=tree_a)

    # Создаём отдельное дерево tree_b и пробуем импортировать Place tree_a в нём.
    async with session_factory() as session:
        user_b = User(
            email=f"other-{uuid.uuid4().hex[:6]}@example.com",
            external_auth_id=f"local:other-{uuid.uuid4().hex[:6]}",
            display_name="Other",
            locale="en",
        )
        session.add(user_b)
        await session.flush()
        tree_b = Tree(
            owner_user_id=user_b.id,
            name="Other tree",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree_b)
        await session.flush()
        await session.commit()
        tree_b_id = tree_b.id

    stub = _StubCommonsClient([_img()])
    async with session_factory() as session:
        with pytest.raises(PlaceNotFoundError):
            await import_wikimedia_for_place(
                session,
                tree_id=tree_b_id,
                place_id=place_a,  # принадлежит tree_a
                user_agent="ua",
                commons_client=stub,
            )


async def test_no_images_returned_yields_zero_stats(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Commons вернул пустой список → fetched=created=0, без insert'ов."""
    tree_id, _owner = fresh_tree
    place_id = await _make_place(session_factory, tree_id=tree_id)
    stub = _StubCommonsClient([])

    async with session_factory() as session:
        stats = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="ua",
            commons_client=stub,
        )
        await session.commit()

    assert stats.fetched == 0
    assert stats.created == 0
    assert stats.skipped_existing == 0

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


async def test_image_without_license_is_still_imported(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """License=None всё ещё импортируется (Commons иногда отдаёт без LicenseShortName).

    Compliance check: provenance должен честно отразить отсутствие license,
    а не подделать его.
    """
    tree_id, _owner = fresh_tree
    place_id = await _make_place(session_factory, tree_id=tree_id)
    stub = _StubCommonsClient([_img(license_short=None)])

    async with session_factory() as session:
        stats = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="ua",
            commons_client=stub,
        )
        await session.commit()

    assert stats.created == 1
    async with session_factory() as session:
        mm = (
            await session.execute(
                select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
            )
        ).scalar_one()
        assert mm.provenance["license_short_name"] is None
        # object_metadata тоже не должен содержать поддельный license.
        assert "license_short_name" not in mm.object_metadata


# ---------------------------------------------------------------------------
# GDPR / privacy structural check
# ---------------------------------------------------------------------------


async def test_no_pii_about_living_persons_in_imported_data(
    session_factory: Any, fresh_tree: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Структурный GDPR-check: importer не пишет ни person_id, ни user-PII в provenance/metadata.

    Wikimedia изображения относятся к местам, не к persons; importer
    должен сохранять чистое разделение entity_type='place' и не утечь
    person-references в impl.
    """
    tree_id, _owner = fresh_tree
    place_id = await _make_place(session_factory, tree_id=tree_id)
    stub = _StubCommonsClient([_img()])

    async with session_factory() as session:
        await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent="ua",
            commons_client=stub,
        )
        await session.commit()

    async with session_factory() as session:
        mm = (
            await session.execute(
                select(MultimediaObject).where(MultimediaObject.tree_id == tree_id)
            )
        ).scalar_one()
        # Структурно убеждаемся, что provenance не содержит person-related ключей.
        forbidden_keys = {"person_id", "user_id", "owner_user_id", "email"}
        assert not (forbidden_keys & set(mm.provenance.keys()))
        # А EntityMultimedia link — только place.
        link = (
            await session.execute(
                select(EntityMultimedia).where(EntityMultimedia.multimedia_id == mm.id)
            )
        ).scalar_one()
        assert link.entity_type == "place"
