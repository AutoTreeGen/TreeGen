"""Integration tests for user account settings (Phase 4.10b, ADR-0038).

Покрывает:

* GET /users/me — профиль текущего user'а.
* PATCH /users/me — display_name / locale / timezone, валидация locale.
* POST /users/me/erasure-request — stub: row создаётся, 202; email
  must match; повторный pending → 409.
* POST /users/me/export-request — stub: row создаётся, 202; повторный
  → 409.
* GET /users/me/requests — list user's own requests, изоляция между
  user'ами.

Auth — через ``conftest._override_auth`` (создаёт fake clerk user
с ``clerk_user_id="user_test_clerk_sub"``, JIT-flow).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models.orm import User, UserActionRequest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_test_user_state(postgres_dsn: str) -> Any:
    """Reset the fake test user's mutable state before each test.

    Conftest's ``_override_auth`` JIT-creates one shared user per
    ``clerk_user_id="user_test_clerk_sub"``. Tests in this file mutate
    that user (display_name / locale) and create ``user_action_requests``
    rows; without reset, later tests see stale state from earlier ones.

    Strategy: before each test, find that user (or no-op if it doesn't
    exist yet — first auth-call will JIT-create), delete all their
    ``user_action_requests``, reset display_name/locale/timezone.
    """
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        existing = (
            await session.execute(select(User).where(User.clerk_user_id == "user_test_clerk_sub"))
        ).scalar_one_or_none()
        if existing is not None:
            await session.execute(
                delete(UserActionRequest).where(UserActionRequest.user_id == existing.id)
            )
            existing.display_name = "Test User"
            existing.locale = "en"
            existing.timezone = None
            await session.commit()
    await engine.dispose()
    yield


# ---------------------------------------------------------------------------
# GET /users/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_me_returns_jit_created_profile(app_client) -> None:
    """auto-override JIT-создал user'а; GET /users/me возвращает её."""
    response = await app_client.get("/users/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clerk_user_id"] == "user_test_clerk_sub"
    assert body["email"] == "owner@autotreegen.local"
    assert body["locale"] == "en"
    assert body["timezone"] is None


# ---------------------------------------------------------------------------
# PATCH /users/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_me_updates_display_name_and_timezone(app_client) -> None:
    response = await app_client.patch(
        "/users/me",
        json={"display_name": "Alice Test", "timezone": "Europe/Moscow"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Alice Test"
    assert body["timezone"] == "Europe/Moscow"


@pytest.mark.asyncio
async def test_patch_me_blank_display_name_clears_to_null(app_client) -> None:
    """Empty/whitespace display_name → backend stores NULL."""
    await app_client.patch("/users/me", json={"display_name": "Bob"})
    response = await app_client.patch("/users/me", json={"display_name": "   "})
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] is None


@pytest.mark.asyncio
async def test_patch_me_accepts_supported_locale(app_client) -> None:
    response = await app_client.patch("/users/me", json={"locale": "ru"})
    assert response.status_code == 200, response.text
    assert response.json()["locale"] == "ru"


@pytest.mark.asyncio
async def test_patch_me_rejects_unknown_locale(app_client) -> None:
    response = await app_client.patch("/users/me", json={"locale": "fr"})
    assert response.status_code == 422, response.text
    assert "locale" in response.text.lower()


@pytest.mark.asyncio
async def test_patch_me_rejects_unknown_field(app_client) -> None:
    """``extra='forbid'`` → unknown fields → 422."""
    response = await app_client.patch("/users/me", json={"unknown": "x"})
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# POST /users/me/erasure-request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_erasure_request_creates_pending_row(app_client, session_factory) -> None:
    response = await app_client.post(
        "/users/me/erasure-request",
        json={"confirm_email": "owner@autotreegen.local"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "erasure"
    assert body["status"] == "pending"
    request_id = uuid.UUID(body["request_id"])

    # Проверяем, что row реально в БД.
    factory = session_factory
    async with factory() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
        assert row.kind == "erasure"
        assert row.status == "pending"


@pytest.mark.asyncio
async def test_erasure_request_rejects_wrong_confirm_email(app_client) -> None:
    response = await app_client.post(
        "/users/me/erasure-request",
        json={"confirm_email": "attacker@evil.example"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_erasure_request_409_when_already_pending(app_client) -> None:
    first = await app_client.post(
        "/users/me/erasure-request",
        json={"confirm_email": "owner@autotreegen.local"},
    )
    assert first.status_code == 202
    second = await app_client.post(
        "/users/me/erasure-request",
        json={"confirm_email": "owner@autotreegen.local"},
    )
    assert second.status_code == 409, second.text


# ---------------------------------------------------------------------------
# POST /users/me/export-request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_request_creates_pending_row(app_client, session_factory) -> None:
    response = await app_client.post("/users/me/export-request", json={})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "export"
    assert body["status"] == "pending"

    factory = session_factory
    request_id = uuid.UUID(body["request_id"])
    async with factory() as session:
        row = (
            await session.execute(
                select(UserActionRequest).where(UserActionRequest.id == request_id)
            )
        ).scalar_one()
        assert row.kind == "export"


@pytest.mark.asyncio
async def test_export_request_409_when_pending(app_client) -> None:
    first = await app_client.post("/users/me/export-request", json={})
    assert first.status_code == 202
    second = await app_client.post("/users/me/export-request", json={})
    assert second.status_code == 409, second.text


# ---------------------------------------------------------------------------
# GET /users/me/requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_my_requests_returns_only_own_user_rows(app_client, session_factory) -> None:
    """Создаём другого user'а с request'ом — он НЕ должен попасть в GET /users/me/requests."""
    factory = session_factory
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session:
        other = User(
            email=f"other-{suffix}@example.com",
            external_auth_id=f"clerk:other_{suffix}",
            clerk_user_id=f"other_clerk_{suffix}",
            display_name="Other",
            locale="en",
        )
        session.add(other)
        await session.flush()
        session.add(
            UserActionRequest(
                user_id=other.id,
                kind="export",
                status="pending",
                request_metadata={},
            )
        )
        await session.commit()
        other_user_id = other.id

    # Создаём свой request через API.
    own = await app_client.post("/users/me/export-request", json={})
    assert own.status_code == 202

    # Список — только наши.
    response = await app_client.get("/users/me/requests")
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(item["id"] != str(other_user_id) for item in body["items"])
    # Только один — собственный.
    assert len(body["items"]) == 1
    assert body["items"][0]["kind"] == "export"
    assert body["items"][0]["status"] == "pending"
