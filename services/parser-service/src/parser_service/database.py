"""Async engine + session factory.

Engine создаётся один раз при старте приложения; сессии — per-request
через FastAPI Depends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# Глобальный engine, инициализируется в lifespan.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> AsyncEngine:
    """Создать async engine + session factory. Идемпотентно для тестов."""
    global _engine, _session_factory  # noqa: PLW0603
    _engine = create_async_engine(database_url, echo=False, future=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose_engine() -> None:
    """Закрыть engine при shutdown."""
    global _engine, _session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    """Engine для тестов / фоновых задач."""
    if _engine is None:
        msg = "Engine not initialized; call init_engine() first."
        raise RuntimeError(msg)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: даёт async-сессию на запрос с auto-commit/rollback."""
    if _session_factory is None:
        msg = "Session factory not initialized; call init_engine() first."
        raise RuntimeError(msg)
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
