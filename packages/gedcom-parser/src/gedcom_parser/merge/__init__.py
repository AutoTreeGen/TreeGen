"""Safe Merge applier (Phase 5.7b).

Берёт ``DiffReport`` (Phase 5.7a) и применяет его к target-дереву с
conflict-aware resolution. Все DB-записи делаются атомарно через savepoint:
если хоть один ``missing_anchor`` обнаружен — merge прерывается, ни одна
запись не материализуется.

Public API:

* :func:`apply_diff_pure` — чистая функция, оперирует in-memory snapshot'ом
  (``TreeSnapshot``), не зависит от SQLAlchemy. Используется и юнит-тестами,
  и DB-адаптером.
* :func:`apply_diff_to_session` — async-обёртка над :func:`apply_diff_pure`,
  которая:

    1. Читает текущее состояние target-дерева из БД и строит ``TreeSnapshot``.
    2. Прогоняет :func:`apply_diff_pure`.
    3. Если результат не aborted — атомарно материализует все ``Change`` в БД
       внутри ``session.begin_nested()``; иначе возвращает результат без
       побочных эффектов.

* :class:`DiffReport`, :class:`MergePolicy`, :class:`MergeResult`,
  :class:`Conflict`, :class:`Change`, :class:`Audit` — Pydantic-типы.

.. note::
   ``DiffReport`` сейчас определён локально, потому что Phase 5.7a
   (``gedcom_parser.diff``) ещё не приземлился в main. Когда landed —
   импорт должен переехать на ``from gedcom_parser.diff import DiffReport``
   с минимальным diff (см. TODO в :mod:`gedcom_parser.merge.types`).
"""

from __future__ import annotations

from gedcom_parser.merge.applier import (
    apply_diff_pure,
    apply_diff_to_session,
)
from gedcom_parser.merge.types import (
    Audit,
    AuditAction,
    Change,
    ChangeKind,
    Conflict,
    ConflictType,
    DiffReport,
    FieldChange,
    MergePolicy,
    MergeResult,
    OnConflict,
    PersonAdd,
    PersonModify,
    PersonRecord,
    PersonRemove,
    RelationAdd,
    RelationRecord,
    RelationRemove,
    RelationType,
    TreeSnapshot,
)

__all__ = [
    "Audit",
    "AuditAction",
    "Change",
    "ChangeKind",
    "Conflict",
    "ConflictType",
    "DiffReport",
    "FieldChange",
    "MergePolicy",
    "MergeResult",
    "OnConflict",
    "PersonAdd",
    "PersonModify",
    "PersonRecord",
    "PersonRemove",
    "RelationAdd",
    "RelationRecord",
    "RelationRemove",
    "RelationType",
    "TreeSnapshot",
    "apply_diff_pure",
    "apply_diff_to_session",
]
