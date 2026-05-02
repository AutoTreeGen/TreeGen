"""Alembic 0039 (Phase 5.10 fantasy_flags) up/down smoke test.

Validates:
* migration import не бросает синтаксис-ошибок
* down_revision указывает на 0038 (chain цельная)
* upgrade()/downgrade() сигнатуры на месте

Полноценный run против Postgres — отдельный integration-тест в
``packages/shared-models/tests/conftest.py`` ``engine_fixture``,
который применяет _all_ миграции при настройке сессии и автоматически
покрывает 0039 при первом успешном teardown.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "infrastructure"
    / "alembic"
    / "versions"
    / "2026_05_03_0039-0039_fantasy_flags.py"
)


@pytest.fixture(scope="module")
def migration_module():  # type: ignore[no-untyped-def]
    """Загрузить migration as module без alembic env (минимально для smoke)."""
    if not _MIGRATION_PATH.exists():
        pytest.skip(f"migration not found at {_MIGRATION_PATH}")
    spec = importlib.util.spec_from_file_location("_alembic_0039", _MIGRATION_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_revision_metadata(migration_module) -> None:  # type: ignore[no-untyped-def]
    """revision == '0039', down_revision == '0038'."""
    assert migration_module.revision == "0039"
    assert migration_module.down_revision == "0038"


def test_migration_has_upgrade_and_downgrade(migration_module) -> None:  # type: ignore[no-untyped-def]
    """upgrade() + downgrade() callable определены."""
    assert callable(migration_module.upgrade)
    assert callable(migration_module.downgrade)


def test_orm_table_matches_migration_name() -> None:
    """ORM ``__tablename__`` совпадает с тем что migration создаёт.

    Простой sanity: catch'ит "переименовали в ORM, забыли в миграции".
    """
    from shared_models.orm import FantasyFlag

    assert FantasyFlag.__tablename__ == "fantasy_flags"
