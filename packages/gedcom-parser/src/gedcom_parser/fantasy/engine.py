"""Fantasy filter engine: rule registry + ``scan_document`` entry point.

Mirror :mod:`gedcom_parser.validator.engine` shape — каждое правило это
класс с ``rule_id`` + ``evaluate`` методом. Default-registry в
:mod:`gedcom_parser.fantasy.rules` собирается явным импортом (без
entry-point auto-discovery — overhead не оправдан для 12 правил).

**Read-only invariant.** Engine обязан передавать ``GedcomDocument``
правилам без изменений. ``scan_document`` НЕ держит mutable state —
правила сами не должны мутировать ``doc`` (Protocol контракт).
``test_no_mutation_invariant`` проверяет это на golden fixture.

**Exception isolation.** Если rule бросает на корректно-распарсенном
документе (bug в правиле, не в данных), engine ловит и эмитит INFO-flag
``rule_id="fantasy_internal_error"`` — лучше потерять одно правило, чем
сорвать весь scan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from gedcom_parser.fantasy.types import (
    FantasyContext,
    FantasyFlag,
    FantasySeverity,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from gedcom_parser.document import GedcomDocument


@runtime_checkable
class FantasyRule(Protocol):
    """Pure function: ``(GedcomDocument, FantasyContext) -> Iterable[FantasyFlag]``.

    Контракт:

    - **Pure / read-only.** Никакого I/O. ``doc`` и ``ctx`` — единственные
      inputs. **Никаких mutations** на ``doc`` или его entities.
    - **Determinism.** Один и тот же ``(doc, ctx)`` → один и тот же список
      flags в стабильном порядке (важно для diff'ов между scan'ами).
    - **No exceptions on valid input.** Rule НЕ должен бросать на корректно-
      распарсенном document'е. Если данные не подходят (нет дат, нет родителей,
      …) — вернуть пустой list. Bug в rule — engine catch'ит и эмитит INFO.

    Attributes:
        rule_id: Стабильный snake_case идентификатор. Изменение —
            breaking change для downstream (UI-фильтры, analytics, dismiss
            по rule_id).
        default_severity: Default-уровень flag'ов от этого правила.
            Конкретные flag'и могут override (e.g. impossible_lifespan
            эскалирует HIGH→CRITICAL при >130 лет).

    Methods:
        evaluate: Применить rule и вернуть нуль или более flags.
    """

    rule_id: str
    default_severity: FantasySeverity

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,
    ) -> Iterable[FantasyFlag]: ...


def scan_document(
    doc: GedcomDocument,
    *,
    rules: Sequence[FantasyRule] | None = None,
    ctx: FantasyContext | None = None,
) -> list[FantasyFlag]:
    """Прогнать fantasy-rules по document'у и собрать flags.

    Args:
        doc: Распарсенный :class:`GedcomDocument`.
        rules: Опционально — кастомный список правил. ``None`` (default)
            использует :func:`gedcom_parser.fantasy.rules.default_rules`.
            Тесты передают подмножество для изоляции одного правила.
        ctx: Опционально — :class:`FantasyContext` с whitelist'ом
            enabled_rules. Если None и rules не передан, все default
            правила активны.

    Returns:
        Список :class:`FantasyFlag` в порядке rules-iteration; внутри
        одного правила — порядок, в котором правило их выдало.
    """
    if rules is None:
        from gedcom_parser.fantasy.rules import default_rules  # noqa: PLC0415

        rules = default_rules()

    if ctx is None:
        ctx = FantasyContext()

    flags: list[FantasyFlag] = []
    for rule in rules:
        # Apply enabled-rules whitelist если задан.
        if ctx.enabled_rules is not None and rule.rule_id not in ctx.enabled_rules:
            continue
        try:
            flags.extend(rule.evaluate(doc, ctx))
        except Exception as exc:
            flags.append(
                FantasyFlag(
                    rule_id="fantasy_internal_error",
                    severity=FantasySeverity.INFO,
                    confidence=0.0,
                    reason=(
                        f"Rule {rule.rule_id!r} raised {type(exc).__name__}: {exc}. "
                        "Skipped this rule for scan; please report the bug."
                    ),
                    evidence={"failed_rule_id": rule.rule_id, "exception_type": type(exc).__name__},
                )
            )
    return flags


__all__ = ["FantasyRule", "scan_document"]
