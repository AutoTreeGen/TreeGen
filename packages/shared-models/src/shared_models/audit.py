"""SQLAlchemy event listeners для записи в audit_log и инкремента version_id.

Использование:

.. code-block:: python

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from shared_models import register_audit_listeners

    SessionMaker = async_sessionmaker(bind=engine)
    register_audit_listeners(SessionMaker, actor_resolver=lambda: current_user_id())

Listener вешается на ``Session`` (sync-класс под капотом async-сессии),
стреляет на ``before_flush`` и пишет audit-запись в той же транзакции.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.orm import Session, attributes

from shared_models.enums import ActorKind, AuditAction
from shared_models.mixins import SoftDeleteMixin, TreeEntityMixins
from shared_models.orm.audit_log import AuditLog
from shared_models.types import new_uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------------------------
# Контекст (тред-локал на сессию). Сервис устанавливает actor перед flush.
# ---------------------------------------------------------------------------

_AUDIT_CONTEXT_KEY = "_audit_context"
_PENDING_AUDIT_KEY = "_pending_audit_entries"
_AUDIT_SKIP_KEY = "_audit_skip"


def set_audit_skip(session: Session, skip: bool) -> None:
    """Включить/выключить запись в audit_log для текущей сессии.

    Используется bulk-импортом: на время массовой вставки выключаем построчный
    audit (он удваивает число INSERT'ов и держит снапшоты в памяти), записывая
    взамен один агрегированный audit-entry уровня import_job.

    Provenance каждой импортированной сущности всё равно сохраняется через
    поле ``provenance`` (jsonb) с ``import_job_id`` — это нерушимый принцип
    Evidence-First (см. ADR-0003).
    """
    session.info[_AUDIT_SKIP_KEY] = skip


def is_audit_skipped(session: Session) -> bool:
    """Проверить, отключён ли audit для текущей сессии."""
    return bool(session.info.get(_AUDIT_SKIP_KEY, False))


class AuditContext:
    """Контекст аудита для одной сессии/транзакции."""

    __slots__ = ("actor_kind", "actor_user_id", "import_job_id", "reason")

    def __init__(
        self,
        actor_user_id: uuid.UUID | None = None,
        actor_kind: ActorKind = ActorKind.SYSTEM,
        import_job_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> None:
        """Инициализация контекста аудита."""
        self.actor_user_id = actor_user_id
        self.actor_kind = actor_kind
        self.import_job_id = import_job_id
        self.reason = reason


def set_audit_context(session: Session, context: AuditContext) -> None:
    """Установить контекст аудита для текущей сессии.

    Сервисный слой вызывает перед коммитом транзакции:

    .. code-block:: python

        async with SessionMaker() as session:
            set_audit_context(session.sync_session, AuditContext(actor_user_id=user.id))
            ...
            await session.commit()
    """
    session.info[_AUDIT_CONTEXT_KEY] = context


def get_audit_context(session: Session) -> AuditContext:
    """Получить контекст или дефолтный (system actor) если не задан."""
    ctx = session.info.get(_AUDIT_CONTEXT_KEY)
    if ctx is None:
        return AuditContext()
    return ctx  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Утилиты построения diff
# ---------------------------------------------------------------------------


def _is_audited(obj: Any) -> bool:
    """Подлежит ли объект аудиту.

    Аудитируем все доменные записи дерева (TreeEntityMixins). Trees — отдельно
    (TreeOwnedMixins, у них нет tree_id, но есть id == tree_id).
    """
    return isinstance(obj, TreeEntityMixins | SoftDeleteMixin)


def _model_columns(obj: Any) -> list[str]:
    """Список имён колонок объекта."""
    return [c.name for c in obj.__table__.columns]


def _to_jsonable(value: Any) -> Any:
    """Привести значение к JSON-сериализуемому виду."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def _snapshot(obj: Any) -> dict[str, Any]:
    """Полный снапшот колонок объекта."""
    return {col: _to_jsonable(getattr(obj, col)) for col in _model_columns(obj)}


def _changed_fields(obj: Any) -> dict[str, dict[str, Any]]:
    """Diff модифицированных колонок: ``{field: {before, after}}``."""
    state = attributes.instance_state(obj)
    changes: dict[str, dict[str, Any]] = {}
    for attr in state.attrs:
        hist = attr.history
        if not hist.has_changes():
            continue
        # ``hist.added`` содержит новое значение, ``hist.deleted`` — старое.
        # Пропускаем relationship-атрибуты (нас интересуют только колонки).
        if attr.key not in {c.name for c in obj.__table__.columns}:
            continue
        before = hist.deleted[0] if hist.deleted else None
        after = hist.added[0] if hist.added else None
        if before == after:
            continue
        changes[attr.key] = {
            "before": _to_jsonable(before),
            "after": _to_jsonable(after),
        }
    return changes


def _resolve_tree_id(obj: Any) -> uuid.UUID | None:
    """Получить tree_id объекта (для trees — это сам id)."""
    tid = getattr(obj, "tree_id", None)
    if tid is not None:
        return tid  # type: ignore[no-any-return]
    # Сам Tree
    if obj.__tablename__ == "trees":
        return obj.id  # type: ignore[no-any-return]
    return None


# ---------------------------------------------------------------------------
# Основной listener
# ---------------------------------------------------------------------------


def _make_audit_entry(
    obj: Any,
    action: AuditAction,
    diff: dict[str, Any],
    ctx: AuditContext,
) -> AuditLog | None:
    """Построить AuditLog-запись или вернуть None если объект не имеет tree_id."""
    tree_id = _resolve_tree_id(obj)
    if tree_id is None:
        return None
    return AuditLog(
        tree_id=tree_id,
        entity_type=obj.__tablename__,
        entity_id=obj.id,
        action=action.value,
        actor_user_id=ctx.actor_user_id,
        actor_kind=ctx.actor_kind.value,
        import_job_id=ctx.import_job_id,
        reason=ctx.reason,
        diff=diff,
    )


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Хук: захватываем diff'ы и проставляем UUID, **не** вставляя audit_log сразу.

    Если для сессии установлен skip-flag (bulk-импорт), всё равно проставляем
    id для новых объектов (нужно для FK), но без накопления pending audit.

    AuditLog имеет FK на trees/users/import_jobs — если вставлять audit-запись
    в той же транзакции что и сам Tree, FK проверяется сразу при INSERT и
    падает с ForeignKeyViolation (Postgres не видит trees-строки в момент
    вставки audit_log, оба INSERT'а ещё в полёте).

    Решение — двухфазный listener:

    1. ``before_flush``: захватываем все изменения в ``session.info[_PENDING_AUDIT_KEY]``,
       при этом проставляем ``id`` и инкрементим ``version_id``.
    2. ``after_flush``:  основные сущности уже в БД, безопасно ``session.add()``
       audit-записей. Они попадут в очередной flush в той же транзакции.
    """
    skip = is_audit_skipped(session)
    pending: list[dict[str, Any]] = session.info.setdefault(_PENDING_AUDIT_KEY, [])

    for obj in list(session.new):
        if not _is_audited(obj) or isinstance(obj, AuditLog):
            continue
        # SQLA применит column defaults внутри flush'а, но нам id нужен сейчас
        # для построения diff и FK-ссылок. Назначаем сами.
        if hasattr(obj, "id") and obj.id is None:
            obj.id = new_uuid()
        if skip:
            continue  # Bulk-режим: id проставили, но audit-запись не строим.
        pending.append(
            {
                "obj": obj,
                "action": AuditAction.INSERT,
                "diff": {
                    "before": None,
                    "after": _snapshot(obj),
                    "fields": _model_columns(obj),
                },
            }
        )

    for obj in list(session.dirty):
        if not _is_audited(obj) or isinstance(obj, AuditLog):
            continue
        if skip:
            continue
        changes = _changed_fields(obj)
        if not changes:
            continue
        # Detect soft-delete vs restore vs regular update.
        action = AuditAction.UPDATE
        if "deleted_at" in changes:
            before, after = changes["deleted_at"]["before"], changes["deleted_at"]["after"]
            if before is None and after is not None:
                action = AuditAction.DELETE
            elif before is not None and after is None:
                action = AuditAction.RESTORE
        # Bump version_id on update.
        if hasattr(obj, "version_id") and "version_id" not in changes:
            obj.version_id = (obj.version_id or 0) + 1
            changes["version_id"] = {
                "before": obj.version_id - 1,
                "after": obj.version_id,
            }
        pending.append(
            {
                "obj": obj,
                "action": action,
                "diff": {
                    "before": None,
                    "after": None,
                    "fields": list(changes.keys()),
                    "changes": changes,
                },
            }
        )

    for obj in list(session.deleted):
        # Hard delete (только GDPR-flow). Audit-запись остаётся с tree_id и id,
        # PII-поля анонимизируются в сервисе ДО delete().
        if not _is_audited(obj) or isinstance(obj, AuditLog):
            continue
        if skip:
            continue
        pending.append(
            {
                "obj": obj,
                "action": AuditAction.DELETE,
                "diff": {
                    "before": _snapshot(obj),
                    "after": None,
                    "fields": _model_columns(obj),
                },
            }
        )


def _after_flush(session: Session, _flush_context: Any) -> None:
    """Хук: после flush'а основных сущностей — вставляем накопленные audit-записи.

    Все entity_id и tree_id теперь существуют в БД, FK будут довольны.
    Audit-объекты попадут в следующий flush в той же транзакции (commit вызывает
    финальный flush автоматически). На повторный flush ``before_flush``
    отфильтрует их через ``isinstance(obj, AuditLog)``.
    """
    ctx = get_audit_context(session)
    pending: list[dict[str, Any]] = session.info.pop(_PENDING_AUDIT_KEY, [])
    for item in pending:
        entry = _make_audit_entry(item["obj"], item["action"], item["diff"], ctx)
        if entry is not None:
            session.add(entry)


def register_audit_listeners(
    session_factory: async_sessionmaker[Any] | type[Session],
    *,
    actor_resolver: Callable[[], uuid.UUID | None] | None = None,
) -> None:
    """Вешает audit-listener на фабрику сессий.

    Args:
        session_factory: ``async_sessionmaker`` или класс ``Session``.
        actor_resolver: опциональная функция, возвращающая текущего user_id.
            Если задана, используется как дефолт когда явный context не установлен.
    """
    # async_sessionmaker оборачивает sync Session — listener надо вешать на класс sync Session.
    # Передаём напрямую Session (или sync_session_class из async_sessionmaker).
    target: type[Session]
    if hasattr(session_factory, "sync_session_class"):
        target = session_factory.sync_session_class
    elif isinstance(session_factory, type) and issubclass(session_factory, Session):
        target = session_factory
    else:
        target = Session  # fallback на глобальный класс

    event.listen(target, "before_flush", _before_flush)
    event.listen(target, "after_flush", _after_flush)

    if actor_resolver is not None:
        # Если задан резолвер — вешаем хук, который при создании сессии
        # подставляет дефолтный контекст.
        @event.listens_for(target, "after_begin")
        def _on_begin(session: Session, _txn: Any, _conn: Any) -> None:
            if _AUDIT_CONTEXT_KEY not in session.info:
                user_id = actor_resolver()
                set_audit_context(
                    session,
                    AuditContext(
                        actor_user_id=user_id,
                        actor_kind=ActorKind.USER if user_id else ActorKind.SYSTEM,
                    ),
                )
