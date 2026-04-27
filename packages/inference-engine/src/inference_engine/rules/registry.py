"""Module-level registry для InferenceRule плагинов.

Phase 7.0 — explicit registration: caller импортирует rule и зовёт
``register_rule(MyRule())``. Phase 7.1+ может добавить auto-discovery
через entry points (``[project.entry-points."autotreegen.inference_rules"]``
в pyproject.toml каждого пакета-rule), но это отложено — для одного
пакета inference-engine + горстки rules в Phase 7.0 explicit-подход
проще и тестируется без mock'инга entry-point loader'ов.

Registry — module-level dict. Threading: для CPython с GIL чтение
``_registry`` потокобезопасно (атомарный dict-lookup), запись через
``register_rule()`` — нет, но мы и не ожидаем concurrent registration:
rules регистрируются один раз при инициализации (например, в FastAPI
startup event в Phase 7.3).
"""

from __future__ import annotations

from inference_engine.rules.base import InferenceRule

_registry: dict[str, InferenceRule] = {}


class RuleAlreadyRegisteredError(ValueError):
    """Rule с таким rule_id уже зарегистрирован.

    Регистрация двух разных объектов с одинаковым ``rule_id`` ломает
    provenance: в Evidence записан один rule_id, а композиция не сможет
    однозначно сказать, какой объект его произвёл. Поэтому повторная
    регистрация — ошибка, а не silent overwrite.
    """


class RuleNotFoundError(KeyError):
    """Rule с таким rule_id не зарегистрирован."""


def register_rule(rule: InferenceRule) -> None:
    """Зарегистрировать rule в module-level registry.

    Args:
        rule: Любой объект, удовлетворяющий InferenceRule Protocol
            (есть атрибут ``rule_id: str`` и метод ``apply()``).

    Raises:
        TypeError: Если объект не подходит под InferenceRule Protocol.
        RuleAlreadyRegisteredError: Если rule с таким ``rule_id`` уже
            зарегистрирован. Используй ``clear_registry()`` или
            ``unregister_rule()`` если нужен replace.
    """
    if not isinstance(rule, InferenceRule):
        msg = (
            f"Object {rule!r} does not satisfy InferenceRule Protocol "
            "(needs rule_id: str and apply() method)."
        )
        raise TypeError(msg)
    if rule.rule_id in _registry:
        msg = f"Rule {rule.rule_id!r} is already registered."
        raise RuleAlreadyRegisteredError(msg)
    _registry[rule.rule_id] = rule


def unregister_rule(rule_id: str) -> None:
    """Удалить rule из registry. No-op если не зарегистрирован."""
    _registry.pop(rule_id, None)


def get_rule(rule_id: str) -> InferenceRule:
    """Получить зарегистрированный rule по идентификатору.

    Raises:
        RuleNotFoundError: Если rule не зарегистрирован.
    """
    try:
        return _registry[rule_id]
    except KeyError as exc:
        msg = f"Rule {rule_id!r} is not registered."
        raise RuleNotFoundError(msg) from exc


def all_rules() -> list[InferenceRule]:
    """Список всех зарегистрированных rule's. Порядок — insertion order."""
    return list(_registry.values())


def clear_registry() -> None:
    """Очистить registry. Полезно в тестах между кейсами."""
    _registry.clear()
