"""Backfill ``persons.surname_dm`` / ``persons.given_name_dm`` для исторических данных.

Phase 4.4.1: новые импорты считают DM в ``import_runner``, но ряды,
вставленные до этой миграции, имеют NULL в DM-колонках. Этот скрипт
проходит persons пачками по ``--batch`` и заполняет.

Idempotent: пересчитывает DM от Name-записей независимо от текущего
значения колонок. Безопасно перезапускать после каждого крупного импорта,
если он шёл через старый код, или после каких-то ручных правок.

Запуск:

    uv run python scripts/backfill_dm_buckets.py [--tree-id UUID] [--batch 1000] [--only-empty]

Опции:
- ``--tree-id``: ограничить одним деревом. По умолчанию все persons.
- ``--batch``: размер пачки UPDATE (default 1000).
- ``--only-empty``: пересчитывать только ряды где ``surname_dm IS NULL``
  И ``given_name_dm IS NULL`` (быстрее на повторных запусках).

Производительность: на 12k персон ~10-20 секунд (одна транзакция per
batch, минимум round-trip'ов). Большая часть времени — selectin Name-load.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

# Раскрытие пути: позволяет запускать скрипт без `uv run --package` —
# `python scripts/backfill_dm_buckets.py` тоже работает.
_REPO_ROOT = Path(__file__).resolve().parents[1]
for sub in (
    _REPO_ROOT / "packages" / "shared-models" / "src",
    _REPO_ROOT / "packages" / "entity-resolution" / "src",
    _REPO_ROOT / "services" / "parser-service" / "src",
):
    if sub.exists() and str(sub) not in sys.path:
        sys.path.insert(0, str(sub))

from parser_service.services.dm_buckets import merge_dm_buckets  # noqa: E402
from shared_models.orm import Name, Person  # noqa: E402
from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill DM phonetic buckets on persons")
    parser.add_argument(
        "--tree-id",
        type=uuid.UUID,
        default=None,
        help="Ограничить одним деревом. По умолчанию все persons.",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=1000,
        help="Сколько персон обрабатывать за один UPDATE-цикл (default 1000).",
    )
    parser.add_argument(
        "--only-empty",
        action="store_true",
        help="Пересчитывать только ряды где обе DM-колонки NULL.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL")
        or os.environ.get("AUTOTREEGEN_DATABASE_URL")
        or "postgresql+asyncpg://autotreegen:autotreegen@localhost:5432/autotreegen",
        help="Async DSN. По умолчанию env DATABASE_URL или local dev.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    """Прогнать backfill. Возвращает count обновлённых персон."""
    async_url = args.database_url
    # asyncpg не поддерживает `+psycopg`; нормализуем.
    if async_url.startswith("postgresql://"):
        async_url = async_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif async_url.startswith("postgresql+psycopg"):
        async_url = async_url.replace("postgresql+psycopg", "postgresql+asyncpg", 1)

    engine = create_async_engine(async_url, echo=False)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    total_updated = 0
    try:
        async with sessionmaker() as session:
            offset = 0
            while True:
                stmt = select(Person.id).where(Person.deleted_at.is_(None))
                if args.tree_id is not None:
                    stmt = stmt.where(Person.tree_id == args.tree_id)
                if args.only_empty:
                    stmt = stmt.where(
                        Person.surname_dm.is_(None),
                        Person.given_name_dm.is_(None),
                    )
                stmt = stmt.order_by(Person.id).offset(offset).limit(args.batch)
                person_ids = list((await session.execute(stmt)).scalars().all())
                if not person_ids:
                    break

                # Загружаем все Name'ы пачкой одним round-trip'ом.
                names_res = await session.execute(
                    select(Name.person_id, Name.given_name, Name.surname).where(
                        Name.person_id.in_(person_ids)
                    )
                )
                surnames_by_pid: dict[uuid.UUID, list[str]] = {pid: [] for pid in person_ids}
                givens_by_pid: dict[uuid.UUID, list[str]] = {pid: [] for pid in person_ids}
                for pid, given, surname in names_res.all():
                    if surname:
                        surnames_by_pid[pid].append(surname)
                    if given:
                        givens_by_pid[pid].append(given)

                # UPDATE по одному (Postgres-friendly batch UPDATE с
                # массивами — отдельный кейс; здесь N маленьких запросов
                # в одной транзакции, ок для 1000 на батч).
                for pid in person_ids:
                    surname_buckets = merge_dm_buckets(surnames_by_pid[pid]) or None
                    given_buckets = merge_dm_buckets(givens_by_pid[pid]) or None
                    await session.execute(
                        update(Person)
                        .where(Person.id == pid)
                        .values(
                            surname_dm=surname_buckets,
                            given_name_dm=given_buckets,
                        )
                    )
                await session.commit()
                total_updated += len(person_ids)
                print(f"  updated {total_updated} persons so far...", flush=True)
                offset += args.batch
    finally:
        await engine.dispose()

    print(f"Done. Updated {total_updated} persons.")
    return total_updated


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    main()
    raise SystemExit(0)  # exit 0 даже если 0 персон обновлено (idempotent)
