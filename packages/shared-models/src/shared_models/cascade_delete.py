"""Каскадные delete-операции над данными дерева (Phase 4.11b, ADR-0049).

Утилиты для GDPR-erasure pipeline'а:

* :func:`cascade_soft_delete` — soft-delete всех ``TREE_ENTITY_TABLES``
  одного дерева (persons / names / families / events / places / sources /
  notes / multimedia_objects). Записи остаются в БД с ``deleted_at``,
  ``provenance.erasure_request_id`` указывает на user_action_requests-row.
* :func:`hard_delete_dna` — hard-delete DNA-записей user'а (kits, test
  records, consents, imports, matches, shared_matches). Per ADR-0012:
  DNA — special category (GDPR Art. 9), не оставляем soft-delete tombstone.

Всё чисто sqlalchemy — никаких HTTP-зависимостей. Caller (worker) держит
сессию и сам commit'ит/rollback'ит транзакцию.

См. ADR-0049 §«Soft vs hard delete reconcile».
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import delete, select, update

from shared_models.orm import (
    Citation,
    DnaConsent,
    DnaImport,
    DnaKit,
    DnaMatch,
    DnaTestRecord,
    Event,
    Family,
    MultimediaObject,
    Name,
    Note,
    Person,
    Place,
    SharedMatch,
    Source,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Источник правды для tree-domain entity tables — синхронизирован с
# ``tests/test_schema_invariants.py::TREE_ENTITY_TABLES``. При добавлении
# новой tree-entity таблицы (Phase X) сюда обязательно тоже добавить —
# иначе erasure pipeline пропустит её и оставит «осиротевшие» PII записи.
#
# Не включаем ``trees`` (само дерево soft-удаляется отдельно через
# ``Tree.deleted_at`` в worker'е) и ``hypotheses`` (FK CASCADE через
# tree, плюс собственные provenance/evidence линки — обрабатывается
# в Phase 4.11c вместе с ownership transfer).
_TREE_DOMAIN_MODELS: Final = (
    Person,
    Family,
    Event,
    Place,
    Source,
    Citation,
    Note,
    MultimediaObject,
)

# Подсущности (FK на родителя, без собственного ``tree_id`` колонки):
# soft-удаляются по совпадению parent_id с уже soft-удалёнными родителями.
# Phase 4.11b: ``names`` (person_id), ``place_aliases`` (place_id).
# Не включаем ``family_children`` / ``event_participants`` — они без
# soft-delete (CASCADE через FK при hard-delete родителя).


@dataclass(frozen=True, slots=True)
class CascadeSoftDeleteResult:
    """Сводка результата ``cascade_soft_delete`` — для audit metadata.

    Attributes:
        tree_id: Дерево, которое soft-удалили.
        counts: ``{table_name: rows_marked_deleted}``. Используется
            аудитом для verifiable «удалили N persons, M sources, ...».
        total_rows: Сумма по counts — удобно для quick logging.
    """

    tree_id: uuid.UUID
    counts: dict[str, int]
    total_rows: int


async def cascade_soft_delete(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    deleted_by_user_id: uuid.UUID,
    erasure_request_id: uuid.UUID,
    reason: str = "gdpr_erasure",
    now: dt.datetime | None = None,
) -> CascadeSoftDeleteResult:
    """Soft-delete всех tree-domain записей одного дерева.

    Идемпотентно: уже soft-deleted записи (``deleted_at IS NOT NULL``)
    не перезаписываются — это сохраняет original timestamp удаления.

    Алгоритм:

    1. Для каждой модели в ``_TREE_DOMAIN_MODELS``: выполнить bulk-update
       ``UPDATE ... SET deleted_at=:now,
       provenance = provenance || jsonb_build_object('erasure_request_id', ...,
       'erasure_reason', ..., 'erased_by_user_id', ...)
       WHERE tree_id = :tree_id AND deleted_at IS NULL``.
    2. Под-сущности (``Name`` для persons): soft-delete через FK
       parent_id ∈ persons этого дерева.

    Caller responsible for ``await session.commit()``.

    Args:
        session: Активная async-сессия. Ожидаем уже открытую транзакцию.
        tree_id: Дерево к удалению.
        deleted_by_user_id: User, инициировавший erasure (для audit
          provenance pointer'а).
        erasure_request_id: ``user_action_requests.id`` — обратная ссылка
          для traceability (Phase 4.11c восстановление, support requests).
        reason: Свободная строка, попадает в ``provenance.erasure_reason``.
          Дефолт ``"gdpr_erasure"``; admin-инициированные удаления могут
          использовать ``"admin_takedown"`` etc.
        now: Override timestamp (для тестов). По дефолту — ``UTC now()``.

    Returns:
        :class:`CascadeSoftDeleteResult` — counts по таблицам.
    """
    if now is None:
        now = dt.datetime.now(dt.UTC)
    provenance_patch = {
        "erasure_request_id": str(erasure_request_id),
        "erasure_reason": reason,
        "erased_by_user_id": str(deleted_by_user_id),
        "erased_at": now.isoformat(),
    }

    counts: dict[str, int] = {}
    for model in _TREE_DOMAIN_MODELS:
        table_name = model.__tablename__
        rowcount = await _soft_delete_by_tree(
            session,
            model,
            tree_id=tree_id,
            now=now,
            provenance_patch=provenance_patch,
        )
        counts[table_name] = rowcount

    # ``names`` (sub-entity без tree_id): soft-delete через person_id.
    counts["names"] = await _soft_delete_names_for_tree(
        session,
        tree_id=tree_id,
        now=now,
    )

    total = sum(counts.values())
    return CascadeSoftDeleteResult(
        tree_id=tree_id,
        counts=counts,
        total_rows=total,
    )


async def _soft_delete_by_tree(
    session: AsyncSession,
    model: Any,
    *,
    tree_id: uuid.UUID,
    now: dt.datetime,
    provenance_patch: dict[str, Any],
) -> int:
    """Bulk soft-delete для tree-scoped модели; merge provenance jsonb.

    SQL: ``UPDATE tbl SET deleted_at=:now,
    provenance = provenance || :patch
    WHERE tree_id = :tree_id AND deleted_at IS NULL``.

    Provenance merge — JSONB `||` оператор: новые ключи добавляются,
    существующие переопределяются. Сохраняем оригинальные source_files,
    import_job_id, manual_edits.
    """
    from sqlalchemy import type_coerce  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import JSONB  # noqa: PLC0415

    has_provenance = hasattr(model, "provenance")
    stmt = update(model).where(
        model.tree_id == tree_id,
        model.deleted_at.is_(None),
    )
    values: dict[str, Any] = {"deleted_at": now}
    if has_provenance:
        # provenance is NOT NULL (server_default '{}'::jsonb), безопасно
        # без COALESCE'а. Bind patch как JSONB через type_coerce, чтобы
        # `||` оператор сработал с правильным типом.
        values["provenance"] = model.provenance.op("||")(type_coerce(provenance_patch, JSONB))
    stmt = stmt.values(**values)
    result = await session.execute(stmt)
    return getattr(result, "rowcount", 0) or 0


async def _soft_delete_names_for_tree(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    now: dt.datetime,
) -> int:
    """Soft-delete ``names``-rows у persons этого дерева.

    Не имеет ``tree_id`` колонки и ``provenance`` (см. mixins ``names``):
    наследует семантику от родителя. Только ``deleted_at``.
    """
    person_ids_q = select(Person.id).where(Person.tree_id == tree_id)
    stmt = (
        update(Name)
        .where(Name.person_id.in_(person_ids_q), Name.deleted_at.is_(None))
        .values(deleted_at=now)
    )
    result = await session.execute(stmt)
    return getattr(result, "rowcount", 0) or 0


# ---------------------------------------------------------------------------
# DNA hard-delete (ADR-0012 §«Right to be forgotten»)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HardDeleteDnaResult:
    """Сводка hard-delete DNA-записей для audit metadata."""

    counts: dict[str, int]
    total_rows: int


async def hard_delete_dna_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> HardDeleteDnaResult:
    """Hard-delete всех DNA-записей пользователя.

    DNA — special category (GDPR Art. 9, ADR-0012): soft-delete не
    подходит, потому что хранение зашифрованных PII-блобов даже после
    «удаления» нарушает purpose limitation. Hard-delete:

    1. ``dna_matches`` (через kit_id ∈ user's kits) — segment-level data.
    2. ``shared_matches`` (через kit_id) — пиры user's kits.
    3. ``dna_imports`` (created_by_user_id) — историю операций тоже снимаем.
    4. ``dna_test_records`` (user_id) — encrypted blobs metadata.
    5. ``dna_kits`` (owner_user_id) — корневая запись.
    6. ``dna_consents`` (user_id) — consent records (ADR-0012 §«Revocation»).

    Порядок важен: дочерние таблицы → родительские (FK constraints).

    Returns:
        :class:`HardDeleteDnaResult` — counts по таблицам.
    """
    counts: dict[str, int] = {}

    # Сначала собираем kit_ids — нужны для matches/shared_matches.
    kit_ids_rows = (
        (await session.execute(select(DnaKit.id).where(DnaKit.owner_user_id == user_id)))
        .scalars()
        .all()
    )
    kit_ids = list(kit_ids_rows)

    if kit_ids:
        match_stmt = delete(DnaMatch).where(DnaMatch.kit_id.in_(kit_ids))
        counts["dna_matches"] = getattr(await session.execute(match_stmt), "rowcount", 0) or 0
        shared_stmt = delete(SharedMatch).where(SharedMatch.kit_id.in_(kit_ids))
        counts["shared_matches"] = getattr(await session.execute(shared_stmt), "rowcount", 0) or 0
    else:
        counts["dna_matches"] = 0
        counts["shared_matches"] = 0

    imports_stmt = delete(DnaImport).where(DnaImport.created_by_user_id == user_id)
    counts["dna_imports"] = getattr(await session.execute(imports_stmt), "rowcount", 0) or 0

    records_stmt = delete(DnaTestRecord).where(DnaTestRecord.user_id == user_id)
    counts["dna_test_records"] = getattr(await session.execute(records_stmt), "rowcount", 0) or 0

    kits_stmt = delete(DnaKit).where(DnaKit.owner_user_id == user_id)
    counts["dna_kits"] = getattr(await session.execute(kits_stmt), "rowcount", 0) or 0

    consents_stmt = delete(DnaConsent).where(DnaConsent.user_id == user_id)
    counts["dna_consents"] = getattr(await session.execute(consents_stmt), "rowcount", 0) or 0

    total = sum(counts.values())
    return HardDeleteDnaResult(counts=counts, total_rows=total)


__all__ = [
    "CascadeSoftDeleteResult",
    "HardDeleteDnaResult",
    "cascade_soft_delete",
    "hard_delete_dna_for_user",
]
