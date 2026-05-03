"""CompletenessAssertion + CompletenessAssertionSource (Phase 15.11a).

См. ADR-0076 «Completeness assertions / sealed sets».

Asserted-negation: пользователь утверждает, что узкий scope вокруг анкорной
персоны *исчерпан* — например, «у Якова известны все sibling'и (4 человека)»,
«у Сары все children учтены». Каждое утверждение source-backed (≥1 source
рекомендуется; enforcement приходит в 15.11b), revocable (DELETE-эндпоинт
сбрасывает ``is_sealed=False`` и чистит junction, но row сохраняется для
audit) и привязано к user'у-автору через ``asserted_by``.

Downstream-консьюмеры (Phase 15.11c — read-side helpers + интеграция):

* **15.3 Hypothesis Sandbox** — пропускает гипотезы внутри уже-исчерпанных
  scope'ов.
* **15.5 Archive Search Planner** — не генерирует архивные search-tasks
  для closed scope'ов.
* **15.6 Court-Ready PDF** — рендерит «proof of negative evidence» секцию.
* **10.7 AI Tree Context Pack** — packs assertion'ы в LLM-контекст.

Source-count invariant (≥1) НЕ ENFORCED на уровне БД — Postgres не выражает
«≥1 row в child table» без триггеров. Service-layer проверка в
``parser_service.api.completeness`` отмечена TODO для 15.11b.

Конвенция FK: ``tree_id`` через ``TreeScopedMixin`` — RESTRICT по
проекту (брифовый CASCADE отвергнут как противоречащий ADR-0003 soft-delete-
first паттерну; tree-purge должен явно очищать assertion'ы). ``subject_person_id``
— RESTRICT по той же причине; merge/delete person требует явного решения,
что делать с её assertion'ами. ``asserted_by`` (users.id) — SET NULL, чтобы
GDPR-erasure не уничтожал генеалогический факт.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.enums import CompletenessScope
from shared_models.mixins import TreeEntityMixins


class CompletenessAssertion(TreeEntityMixins, Base):
    """Утверждение об исчерпанности scope'а вокруг анкорной персоны."""

    __tablename__ = "completeness_assertions"
    __table_args__ = (
        UniqueConstraint(
            "tree_id",
            "subject_person_id",
            "scope",
            "deleted_at",
            name="uq_completeness_assertion_tree_person_scope",
        ),
    )

    subject_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    is_sealed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    asserted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    asserted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    sources: Mapped[list[CompletenessAssertionSource]] = relationship(
        "CompletenessAssertionSource",
        back_populates="assertion",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CompletenessAssertionSource(Base):
    """Junction: одна assertion ↔ N source citations.

    Чистая m2m без mixin'ов и soft-delete: revoke на API-слое (DELETE) очищает
    junction-rows hard-delete'ом, родительская assertion остаётся для audit.
    Service-table в schema_invariants allowlist (не TREE_ENTITY_TABLES) —
    нет tree_id/provenance/version_id, как у ``family_children``.
    """

    __tablename__ = "completeness_assertion_sources"

    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("completeness_assertions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        primary_key=True,
    )

    assertion: Mapped[CompletenessAssertion] = relationship(
        "CompletenessAssertion",
        back_populates="sources",
    )


# ---------------------------------------------------------------------------
# Read-side helpers (Phase 15.11c / ADR-0082)
# ---------------------------------------------------------------------------
#
# Колокальны с ORM-моделью, чтобы любой service (parser-service, archive-service,
# inference-engine consumers и т.д.) импортировал один и тот же код через
# ``shared_models.orm.completeness_assertion``. Validation-слой (write-side)
# наоборот живёт в ``parser_service.completeness.validation`` — см. ADR-0077:
# «validation lives with the service that owns the operation, not with the ORM».
# Read-side helper'ам этот принцип симметричен: запросы к ORM живут с ORM.


async def is_scope_sealed(
    session: AsyncSession,
    person_id: uuid.UUID,
    scope: CompletenessScope | str,
) -> bool:
    """Возвращает ``True``, если указанный scope-анкор для персоны опечатан.

    «Sealed» = существует active (``deleted_at IS NULL``) ``CompletenessAssertion``
    с ``is_sealed=True`` для (person, scope). Revoked assertion'ы (``is_sealed=False``)
    оставляют row для audit, но не считаются sealed — мы фильтруем по флагу.

    Используется консьюмерами (Phase 15.11c — Evidence Panel, Research Log /
    Archive Search Planner, Hypothesis Sandbox, AI Tree Context Pack), чтобы
    пропускать suggestion-логику внутри уже-исчерпанных scope'ов («не предлагать
    больше siblings, если все 4 уже подтверждены и owner это закрепил»).

    Args:
        session: AsyncSession (caller-managed, no commit/rollback здесь).
        person_id: UUID персоны-анкора.
        scope: Член :class:`CompletenessScope` или его ``.value``-строка
            (``"siblings"`` / ``"children"`` / ``"spouses"`` / ``"parents"``).

    Returns:
        ``True`` если есть active sealed assertion, иначе ``False``.

    Raises:
        ValueError: Если ``scope`` строка и не совпадает с известным enum-значением.
    """
    scope_value = _scope_to_value(scope)
    stmt = (
        select(CompletenessAssertion.id)
        .where(
            CompletenessAssertion.subject_person_id == person_id,
            CompletenessAssertion.scope == scope_value,
            CompletenessAssertion.is_sealed.is_(True),
            CompletenessAssertion.deleted_at.is_(None),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def sealed_scopes_for_person(
    session: AsyncSession,
    person_id: uuid.UUID,
) -> frozenset[CompletenessScope]:
    """Все активные sealed scope'ы для персоны (одним SQL).

    Удобнее ``is_scope_sealed`` если консьюмер собирается фильтровать
    несколько scope'ов сразу (например, AI Tree Context Pack строит
    «who's missing» промпт по siblings/children/spouses/parents).

    Возвращает ``frozenset`` для immutable-by-design downstream-логики
    («filter membership check»).
    """
    stmt = select(CompletenessAssertion.scope).where(
        CompletenessAssertion.subject_person_id == person_id,
        CompletenessAssertion.is_sealed.is_(True),
        CompletenessAssertion.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    raw_scopes: Iterable[str] = result.scalars().all()
    out: set[CompletenessScope] = set()
    for raw in raw_scopes:
        try:
            out.add(CompletenessScope(raw))
        except ValueError:
            # Незнакомая строка в БД (новый scope, который пока не в Python-enum'е).
            # Тихо пропускаем — лучше вернуть подмножество, чем падать у каждого
            # консьюмера. Авто-уведомления о таких рассинхронизациях ловит
            # schema_invariants test, но not at runtime.
            continue
    return frozenset(out)


def _scope_to_value(scope: CompletenessScope | str) -> str:
    """Cast scope-input в DB-string. Defensive — отвергает невалидные строки."""
    if isinstance(scope, CompletenessScope):
        return scope.value
    # Validate string is a known enum value. Raises ValueError if not.
    return CompletenessScope(scope).value
