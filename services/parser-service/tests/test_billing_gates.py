"""Тест feature-gating'а в parser-service (Phase 12.0).

Проверяет, что при ``BILLING_ENABLED=true`` без подписки FS-import
endpoint отдаёт 402 со structured detail.

Существующие тесты (test_imports_api.py и т.п.) работают в bypass-режиме
через autouse fixture в conftest — это ОК, гейтинг отдельная concern.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def _billing_enabled_for_test() -> object:
    """Локально включить billing для одного теста; восстановить после."""
    saved = os.environ.get("BILLING_SERVICE_BILLING_ENABLED")
    os.environ["BILLING_SERVICE_BILLING_ENABLED"] = "true"
    try:
        from billing_service.config import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()
    finally:
        if saved is None:
            os.environ.pop("BILLING_SERVICE_BILLING_ENABLED", None)
        else:
            os.environ["BILLING_SERVICE_BILLING_ENABLED"] = saved
        from billing_service.config import get_settings

        get_settings.cache_clear()


@pytest.mark.integration
@pytest.mark.usefixtures("_billing_enabled_for_test")
async def test_fs_import_returns_402_for_free_user(
    app_client: object,
    postgres_dsn: str,
) -> None:
    """POST /imports/familysearch с X-User-Id юзера без подписки → 402."""
    from shared_models.orm import User
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            email="free-fs-test@example.com",
            external_auth_id="local:free-fs-test",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id
    await engine.dispose()

    # Тело сознательно minimal — гейт срабатывает раньше валидации body.
    response = await app_client.post(  # type: ignore[attr-defined]
        "/imports/familysearch",
        headers={"X-User-Id": str(user_id)},
        json={
            "fs_person_id": "KWQS-XX1",
            "tree_id": "00000000-0000-0000-0000-000000000000",
            "access_token": "fake",
            "generations": 4,
        },
    )

    assert response.status_code == 402, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "payment_required"
    assert detail["feature"] == "fs_import_enabled"
    assert detail["current_plan"] == "free"
    assert detail["upgrade_url"].startswith("/pricing")


@pytest.mark.integration
@pytest.mark.usefixtures("_billing_enabled_for_test")
async def test_imports_endpoint_requires_x_user_id_when_billing_enabled(
    app_client: object,
) -> None:
    """POST /imports без X-User-Id при BILLING_ENABLED=true → 401."""
    response = await app_client.post(  # type: ignore[attr-defined]
        "/imports",
        files={"file": ("x.ged", b"0 HEAD\n0 TRLR\n", "application/octet-stream")},
    )
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert "X-User-Id" in detail
