"""Alembic env: async-движок + autogenerate из shared_models.orm.

DATABASE_URL читается из переменной окружения (или alembic.ini), формат:
``postgresql+asyncpg://user:pass@host:5432/db`` для runtime,
``postgresql+psycopg://...`` для offline-режима/sync-скриптов.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Windows + Python 3.13: ProactorEventLoop несовместим с asyncpg/psycopg в
# async-режиме (рвёт SCRAM-auth с ложным InvalidPasswordError). Принудительно
# переключаем на SelectorEventLoop. Должно стоять ДО asyncio.run().
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from alembic import context

# Импорт всех ORM-моделей наполняет Base.metadata.
from shared_models import (
    Base,
    orm,  # noqa: F401  — side effect: регистрация моделей
)
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Автозагрузка .env из корня репо (где лежит alembic.ini). Без этого
# DATABASE_URL/POSTGRES_PASSWORD из .env не попадают в окружение `uv run`.
try:
    from dotenv import load_dotenv

    _repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(_repo_root / ".env", override=False)
except ImportError:
    # python-dotenv опционален — если не установлен, ENV должен быть задан вручную.
    pass

config = context.config

# Override alembic.ini sqlalchemy.url из ENV, если задан.
db_url = os.getenv("DATABASE_URL") or os.getenv("AUTOTREEGEN_DATABASE_URL")
if db_url:
    # Alembic-engine использует async-driver для online, sync для offline.
    config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(obj: object, name: str | None, type_: str, *_args: object) -> bool:
    """Фильтр объектов для autogenerate (исключаем расширения, которые ставит init.sql)."""
    _ = obj, name, type_
    return True


def run_migrations_offline() -> None:
    """Offline-режим: рендерим SQL без подключения к БД."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Sync-обёртка вокруг async-коннекшена для context.run_migrations()."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Online-режим: открываем async-движок и применяем миграции.

    Поддерживаем оба async-драйвера:
    - ``postgresql+asyncpg://`` — нативный, по умолчанию для Linux/macOS.
    - ``postgresql+psycopg://``  — psycopg3 async (нужен на Windows + Python 3.13,
      где asyncpg ломается на SCRAM-auth, см. lessons learned в ADR-0003).

    Bare ``postgresql://`` дополняем asyncpg по умолчанию.
    """
    raw_url = config.get_main_option("sqlalchemy.url") or ""
    if raw_url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
        pass  # async-driver указан явно — оставляем
    elif raw_url.startswith("postgresql://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    config.set_main_option("sqlalchemy.url", raw_url)

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
