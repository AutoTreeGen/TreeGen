"""DeclarativeBase, naming convention и общие SQLAlchemy-типы.

Naming convention важен для Alembic-autogenerate: без него имена констрейнтов
будут случайными и каждая регенерация миграций будет дрейфовать.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

# Шаблоны имён констрейнтов: ix_<table>_<col>, uq_<table>_<col>, fk_..., pk_...
# https://alembic.sqlalchemy.org/en/latest/naming.html
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(AsyncAttrs, DeclarativeBase):
    """Корневой DeclarativeBase для всех ORM-моделей AutoTreeGen.

    - ``AsyncAttrs`` даёт ``await obj.awaitable_attrs.<rel>`` для lazy load в async-режиме.
    - ``MetaData`` с naming-convention — для предсказуемых миграций.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        """Короткий repr вида ``<Person id=...>`` для логов."""
        cls = type(self).__name__
        ident = getattr(self, "id", None)
        return f"<{cls} id={ident!r}>"

    def to_dict(self) -> dict[str, Any]:
        """Сериализация колонок в dict (только колонки, без relationships).

        Используется audit_log для построения ``before``/``after`` snapshot'ов.
        """
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}
