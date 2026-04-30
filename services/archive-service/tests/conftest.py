"""Shared fixtures для archive-service тестов.

Подменяет Redis-фабрику на ``fakeredis`` (паттерн из parser-service),
выключает rate-limiter shared-models на время теста и даёт fresh
``Settings`` через monkeypatch ENV.
"""

from __future__ import annotations

from typing import Any

import pytest

# fakeredis — обязательная test-зависимость archive-service. Если не
# установлена (mostly не воспроизводится в CI) — skip всех тестов.
try:
    import fakeredis.aioredis as fakeredis_aioredis
except ImportError:  # pragma: no cover
    fakeredis_aioredis = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _disable_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """slowapi shared in-memory bucket отравляет state между тестами одного
    процесса (см. shared_models.security). Отключаем глобально.
    """
    monkeypatch.setenv("RATE_LIMITING_ENABLED", "false")


@pytest.fixture
def redis_fake() -> Any:
    """In-memory FakeRedis (с разделяемым server-объектом — общий keyspace)."""
    if fakeredis_aioredis is None:  # pragma: no cover
        pytest.skip("fakeredis not installed")
    server = fakeredis_aioredis.FakeServer()
    return fakeredis_aioredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def patch_redis_factory(monkeypatch: pytest.MonkeyPatch, redis_fake: Any) -> Any:
    """Подменяет ``archive_service.redis_client._redis_client_factory`` на
    функцию, возвращающую ``redis_fake``. Возвращает сам fake — тесты
    могут писать/читать общий keyspace.
    """
    from archive_service import redis_client as redis_client_module

    monkeypatch.setattr(
        redis_client_module,
        "_redis_client_factory",
        lambda: redis_fake,
    )
    return redis_fake


@pytest.fixture
def fs_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Базовые ENV для FS — чтобы quota_configured() == True."""
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "fs_app_test")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://test/callback")
    monkeypatch.setenv("FAMILYSEARCH_BASE_URL", "http://test")


@pytest.fixture
def encryption_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Сгенерировать валидный Fernet key и положить в ENV."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
def clerk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Стаб Clerk issuer (тест-локальный)."""
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")


@pytest.fixture
def adapter(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
) -> Any:
    """Готовый ``FamilySearchAdapter`` (без http_client — будет использован owned)."""
    from archive_service.adapters.familysearch import FamilySearchAdapter
    from archive_service.config import get_settings

    return FamilySearchAdapter(
        settings=get_settings(),
        redis=patch_redis_factory,
    )
