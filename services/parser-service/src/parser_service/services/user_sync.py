"""User sync: Clerk JWT → local users row (Phase 4.10, ADR-0033).

Hot path для всех authed endpoint'ов:

1. ``shared_models.auth.get_current_claims`` верифицирует Bearer JWT и
   отдаёт :class:`ClerkClaims`.
2. :func:`get_or_create_user_from_clerk` мапит ``claims.sub`` →
   :class:`shared_models.orm.User`. Если row нет — создаёт.

Альтернатива: Clerk webhook на ``user.created`` событие создаёт row
эпохально, но webhook может прийти позже первого user-API-вызова
(eventual consistency). Поэтому JIT-create — primary, webhook —
secondary canonical (см. ADR-0033 §«Webhook vs JIT»).

Идемпотентность: повторный вызов с тем же ``clerk_user_id`` возвращает
существующий row (lookup by unique index на :attr:`User.clerk_user_id`).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from shared_models.auth import ClerkClaims
from shared_models.orm import User
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Локаль по умолчанию для свежесозданного user'а. Phase 4.10: берём
# наш дефолт ``en``; user сможет поменять через preferences. Clerk не
# отдаёт locale в JWT-claims (locale-context — у фронта).
_DEFAULT_LOCALE = "en"


async def get_or_create_user_from_clerk(
    session: AsyncSession,
    claims: ClerkClaims,
) -> User:
    """Найти существующего user'а по ``claims.sub`` либо создать нового.

    Email берём из claims если есть; иначе кладём fallback
    ``{sub}@clerk.local`` (валидный по виду, но явно не-доставляем) —
    позднее webhook ``user.updated`` или явный sync-эндпоинт перепишут
    реальным email'ом.

    Args:
        session: AsyncSession (commit на caller'е).
        claims: validated Clerk claims из ``shared_models.auth``.

    Returns:
        :class:`User` — либо существующий, либо только что вставленный.
    """
    existing = await _find_by_clerk_id(session, claims.sub)
    if existing is not None:
        # Бэкфилл email если он раньше отсутствовал, а в JWT теперь есть.
        if claims.email and existing.email.endswith("@clerk.local"):
            existing.email = claims.email
            await session.flush()
        return existing

    email = claims.email or _placeholder_email(claims.sub)
    display_name = _display_name_from_claims(claims)

    # Reconciliation path: user уже мог быть создан non-Clerk-flow'ом
    # (legacy import_runner._ensure_owner, dev-fixture с тем же email'ом,
    # Phase 11.0 owner-fallback). Email — UNIQUE, поэтому повторный
    # insert'нул бы IntegrityError. Если найден row с таким email и
    # ещё без clerk_user_id — claim'им её как Clerk-user, обновляя
    # clerk_user_id и external_auth_id. Это критично для Phase 11.0
    # owner-fallback shim'а (см. ADR-0036): tree, созданный через
    # run_import с email=settings.owner_email, должен принадлежать
    # тому же user'у, что и authed JWT-сессия с тем же email'ом.
    by_email = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if by_email is not None:
        if by_email.clerk_user_id is None:
            by_email.clerk_user_id = claims.sub
            by_email.external_auth_id = f"clerk:{claims.sub}"
            if display_name and by_email.display_name is None:
                by_email.display_name = display_name
            await session.flush()
            logger.info(
                "Reconciled existing user with Clerk: email=%s clerk_user_id=%s users.id=%s",
                email,
                claims.sub,
                by_email.id,
            )
            return by_email
        # row уже имеет clerk_user_id, но не наш — это коллизия (два
        # разных Clerk-аккаунта на один email). По-хорошему — 409;
        # для тестов / dev отдаём существующий row, чтобы не блокировать
        # flow. Production такой ситуации не должно случаться (Clerk
        # enforce'ит email-uniqueness среди своих account'ов).
        logger.warning(
            "Email %s already linked to clerk_user_id=%s; new claim sub=%s ignored",
            email,
            by_email.clerk_user_id,
            claims.sub,
        )
        return by_email

    user = User(
        email=email,
        external_auth_id=f"clerk:{claims.sub}",
        clerk_user_id=claims.sub,
        display_name=display_name,
        locale=_DEFAULT_LOCALE,
    )
    session.add(user)
    await session.flush()
    logger.info(
        "JIT user created from Clerk: clerk_user_id=%s users.id=%s",
        claims.sub,
        user.id,
    )
    return user


async def get_user_id_from_clerk(
    session: AsyncSession,
    claims: ClerkClaims,
) -> uuid.UUID:
    """Convenience: вернуть только ``users.id`` UUID.

    Большинство endpoint'ов хочет именно UUID, не полную row. Делать
    отдельный lookup чтобы не открывать ORM-объект, когда не нужно.
    """
    user = await get_or_create_user_from_clerk(session, claims)
    return user.id


async def _find_by_clerk_id(session: AsyncSession, clerk_user_id: str) -> User | None:
    """Найти user'а по unique ``clerk_user_id``."""
    stmt = select(User).where(User.clerk_user_id == clerk_user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def _placeholder_email(clerk_user_id: str) -> str:
    """Сгенерить deterministic non-routable email для user'ов без email-claim'а.

    Используется для NOT NULL email column'ы в users. Webhook flow
    позже перепишет на реальный.
    """
    return f"{clerk_user_id}@clerk.local"


def _display_name_from_claims(claims: ClerkClaims) -> str | None:
    """Вытащить display_name из claims, если Clerk положил ``first_name``/``name``."""
    first = claims.raw.get("first_name")
    last = claims.raw.get("last_name")
    parts = [str(first) if first else "", str(last) if last else ""]
    full = " ".join(p for p in parts if p).strip()
    if full:
        return full
    name = claims.raw.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    if claims.email:
        return claims.email.split("@", maxsplit=1)[0]
    return None


__all__ = [
    "get_or_create_user_from_clerk",
    "get_user_id_from_clerk",
]
