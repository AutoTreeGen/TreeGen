"""Validator engine: rule registry + ``validate_document`` entry point.

Каждое правило — отдельный класс, удовлетворяющий :class:`ValidatorRule`
Protocol (атрибут ``rule_id`` + метод ``check(doc)``). Pattern зеркалит
``inference_engine.rules.base.InferenceRule`` (Phase 7.0), но с другим
выходом: validator возвращает structured ``Finding``, не вероятностный
``Evidence``.

Дизайн-выбор: registry — module-local list, заполняемый явным импортом
``rules.__init__.py``. Никакого entry-point auto-discovery (overhead для
~10 правил не оправдан). Тесты могут собирать собственный список правил
и звать ``validate_document(doc, rules=[...])`` напрямую без global state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from gedcom_parser.validator.types import Finding, ValidatorContext

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from gedcom_parser.document import GedcomDocument


@runtime_checkable
class ValidatorRule(Protocol):
    """Pure function: ``(GedcomDocument, ValidatorContext) -> Iterable[Finding]``.

    Контракт:

    - **Pure.** Никакого I/O. Чтение `doc` и `ctx` — единственные inputs.
    - **Determinism.** Один и тот же `(doc, ctx)` → один и тот же список
      findings в стабильном порядке (важно для diff'ов между запусками).
    - **No exceptions.** Rule НЕ должен бросать на корректно-распарсенном
      document'е. Если rule не применим (нет нужных полей) — вернуть
      пустой list. Bug в rule — отдельная история, engine ловит и
      превращает в INFO-finding, не падает на impotента.

    Attributes:
        rule_id: Стабильный идентификатор (snake_case). Изменение —
            breaking change для downstream хранилищ findings.

    Methods:
        check: Применить rule к document'у + контексту и вернуть нуль или
            более findings. Большинство правил ``ctx`` игнорируют —
            он нужен только тем, кому требуется raw-records-доступ
            (например, ``MissingXrefRule``).
    """

    rule_id: str

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]: ...


def validate_document(
    doc: GedcomDocument,
    *,
    rules: Sequence[ValidatorRule] | None = None,
    ctx: ValidatorContext | None = None,
) -> list[Finding]:
    """Прогнать validator-rules по document'у и собрать все findings.

    Args:
        doc: Распарсенный :class:`GedcomDocument`.
        rules: Опционально — кастомный список правил. ``None`` (default) —
            используется default-registry из ``gedcom_parser.validator.rules``
            (все правила Phase 5.8). Тесты передают подмножество, чтобы
            изолировать одно правило.
        ctx: Опционально — :class:`ValidatorContext` с дополнительными
            inputs (raw_records). ``None`` (default) — пустой контекст;
            правила, требующие raw_records, тихо вернут [].

    Returns:
        Объединённый list :class:`Finding` от всех правил, в порядке
        ``rules``-iteration. Внутри одного правила — порядок, в котором
        правило их выдало.

    Note:
        Если rule бросил исключение (bug в rule, не в данных), engine
        ловит и эмитит INFO-finding с ``rule_id="validator_internal_error"``
        — лучше тихо потерять одно правило, чем сорвать весь импорт.
    """
    if rules is None:
        from gedcom_parser.validator.rules import default_rules  # noqa: PLC0415

        rules = default_rules()

    if ctx is None:
        ctx = ValidatorContext()

    findings: list[Finding] = []
    for rule in rules:
        try:
            findings.extend(rule.check(doc, ctx))
        except Exception as exc:
            from gedcom_parser.validator.types import Severity  # noqa: PLC0415

            findings.append(
                Finding(
                    rule_id="validator_internal_error",
                    severity=Severity.INFO,
                    message=(
                        f"Internal validator error in rule {rule.rule_id!r}: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    context={"failed_rule_id": rule.rule_id},
                )
            )
    return findings


__all__ = ["ValidatorRule", "validate_document"]
