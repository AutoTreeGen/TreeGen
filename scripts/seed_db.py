"""Создать минимальный набор тестовых данных в локальной БД.

Запуск:
    uv run python scripts/seed_db.py [--reset]

``--reset`` сначала прогоняет ``alembic downgrade base && upgrade head``.
По умолчанию данные добавляются в текущую схему (без сброса).

ENV:
    DATABASE_URL — postgresql+asyncpg://autotreegen:autotreegen@localhost:5432/autotreegen
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

# Windows + Python 3.13: ProactorEventLoop ломает asyncpg/psycopg async (SCRAM).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Автозагрузка .env: иначе DATABASE_URL/POSTGRES_PASSWORD не подхватятся.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

from shared_models import (
    orm,  # noqa: F401  — регистрируем модели
    register_audit_listeners,
)
from shared_models.audit import AuditContext, set_audit_context
from shared_models.enums import (
    ActorKind,
    EntityStatus,
    EventType,
    NameType,
    Sex,
    TreeVisibility,
)
from shared_models.orm import (
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Name,
    Person,
    Place,
    Tree,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _alembic_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "alembic.ini").exists():
            return parent
    sys.exit("ОШИБКА: alembic.ini не найден от scripts/")


def _reset_schema() -> None:
    """alembic downgrade base && upgrade head."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_alembic_root() / "alembic.ini"))
    print("[seed] alembic downgrade base...")
    command.downgrade(cfg, "base")
    print("[seed] alembic upgrade head...")
    command.upgrade(cfg, "head")


async def _seed(database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False, future=True)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)  # noqa: N806
    register_audit_listeners(SessionMaker)

    async with SessionMaker() as session, session.begin():
        set_audit_context(
            session.sync_session,
            AuditContext(actor_kind=ActorKind.SYSTEM, reason="seed_db"),
        )

        # Идемпотентность: если demo-юзер уже есть — переиспользуем (seed-флоу
        # часто запускается повторно при разработке).
        demo_email = "demo@autotreegen.local"
        existing_user = (
            await session.execute(select(User).where(User.email == demo_email))
        ).scalar_one_or_none()
        if existing_user is not None:
            print(f"[seed] demo user already exists: id={existing_user.id}, skipping")
            return

        user = User(
            email=demo_email,
            external_auth_id="seed|demo",
            display_name="Demo User",
            locale="ru",
        )
        session.add(user)
        await session.flush()

        tree = Tree(
            owner_user_id=user.id,
            name="Demo tree",
            visibility=TreeVisibility.PRIVATE.value,
            default_locale="ru",
        )
        session.add(tree)
        await session.flush()

        # 3 поколения: 1 пара родителей -> ребёнок -> внук
        place_minsk = Place(
            tree_id=tree.id,
            canonical_name="Minsk",
            country_code_iso="BY",
            settlement="Minsk",
        )
        session.add(place_minsk)
        await session.flush()

        opa = Person(
            tree_id=tree.id,
            sex=Sex.MALE.value,
            status=EntityStatus.CONFIRMED.value,
            confidence_score=0.95,
            names=[
                Name(
                    given_name="Лев",
                    surname="Иванов",
                    romanized="lev ivanov",
                    name_type=NameType.BIRTH.value,
                )
            ],
        )
        oma = Person(
            tree_id=tree.id,
            sex=Sex.FEMALE.value,
            status=EntityStatus.CONFIRMED.value,
            confidence_score=0.95,
            names=[
                Name(
                    given_name="Мария",
                    surname="Иванова",
                    maiden_surname="Петрова",
                    romanized="maria petrova",
                    name_type=NameType.MARRIED.value,
                ),
            ],
        )
        son = Person(
            tree_id=tree.id,
            sex=Sex.MALE.value,
            status=EntityStatus.CONFIRMED.value,
            confidence_score=0.9,
            names=[Name(given_name="Иван", surname="Иванов", romanized="ivan ivanov")],
        )
        session.add_all([opa, oma, son])
        await session.flush()

        family = Family(
            tree_id=tree.id,
            husband_id=opa.id,
            wife_id=oma.id,
            status=EntityStatus.CONFIRMED.value,
        )
        session.add(family)
        await session.flush()
        session.add(FamilyChild(family_id=family.id, child_person_id=son.id, birth_order=1))

        birth = Event(
            tree_id=tree.id,
            event_type=EventType.BIRTH.value,
            place_id=place_minsk.id,
            date_raw="ABT 1920",
            description="Birth of Ivan",
        )
        session.add(birth)
        await session.flush()
        session.add(EventParticipant(event_id=birth.id, person_id=son.id, role="principal"))

        await session.commit()
        print(f"[seed] OK: user={user.id} tree={tree.id} persons={3}")

    await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Seed local AutoTreeGen DB")
    parser.add_argument(
        "--reset", action="store_true", help="alembic downgrade+upgrade перед сидом"
    )
    args = parser.parse_args(argv)

    if args.reset:
        _reset_schema()

    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://autotreegen:autotreegen@localhost:5432/autotreegen"
    )
    # Bare `postgresql://` — дополняем asyncpg по умолчанию. `+asyncpg` или
    # `+psycopg` оставляем как есть (psycopg3 нужен на Windows + Py3.13).
    if not db_url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    asyncio.run(_seed(db_url))


if __name__ == "__main__":
    main()
