"""Public types for the GEDCOM validator (Phase 5.8).

A :class:`Finding` is one structured issue produced by a single rule against
a single subject (person / family / cross-ref). Findings are advisory — the
import never blocks on them. Severity is informational metadata that callers
(UI, CLI, downstream review) can use to prioritise.

Дизайн-нота: ``Finding`` хранится JSONB-сериализованным внутри
``import_jobs.validation_findings`` (Phase 5.8 миграция 0033). Все поля
должны быть JSON-friendly; никаких ORM-объектов или Pydantic-моделей,
которые пользователь не сможет читать в `psql`. Round-trip:
``Finding.from_dict(f.to_dict()) == f``.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gedcom_parser.models import GedcomRecord


class Severity(enum.StrEnum):
    """Severity-уровень одного finding.

    - ``INFO``: информационное, не требует действий пользователя.
    - ``WARNING``: подозрительно, человек должен взглянуть.
    - ``ERROR``: почти наверняка ошибка данных. Импорт всё равно завершается
      успешно — finding'и advisory. Future-work: опциональный strict-mode
      может конвертировать ERROR в hard-fail.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Finding:
    """Один structured-issue от validator-rule.

    Attributes:
        rule_id: Стабильный идентификатор правила (e.g. ``"mother_age_low"``).
            Используется UI-фильтрами, аналитикой, и вторичными downstream-
            системами. Меняется только через breaking-change ADR.
        severity: :class:`Severity` уровень.
        message: Человекочитаемое сообщение на английском (UI-локализация —
            future Phase 5.8b). Должно быть конкретным: содержать
            обнаруженные значения (возрасты, даты), но не PII полностью.
        person_xref: GEDCOM xref ``Person``-субъекта (``"I12"``), если
            finding привязан к одной персоне. None — finding на семье или
            на cross-ref.
        family_xref: GEDCOM xref ``Family``-субъекта (``"F3"``), если
            finding привязан к семье. Один finding может иметь и
            ``person_xref``, и ``family_xref`` (например, "child birth after
            mother death" привязан и к ребёнку, и к семье через мать).
        suggested_fix: Краткое описание того, что пользователь мог бы
            сделать (e.g. ``"verify mother's birth date or remove this child
            from the family"``). None — fix не очевиден / нет универсального
            рецепта.
        context: Произвольные дополнительные данные для UI / debug
            (e.g. ``{"mother_birth_year": 1850, "child_birth_year": 1810,
            "age_at_birth_years": -40}``). Должно быть JSON-сериализуемо.
    """

    rule_id: str
    severity: Severity
    message: str
    person_xref: str | None = None
    family_xref: str | None = None
    suggested_fix: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-сериализуемое представление для persist'инга в jsonb."""
        data = asdict(self)
        data["severity"] = self.severity.value
        return data


@dataclass(frozen=True, slots=True)
class ValidatorContext:
    """Дополнительные inputs, которые нужны некоторым правилам помимо ``doc``.

    Например, ``MissingXrefRule`` нужна сырая последовательность records
    (``GedcomDocument`` уже отфильтровал записи без xref'а). Большинство
    правил context игнорируют — он default-empty.

    Attributes:
        raw_records: Опциональная плоская последовательность корневых
            ``GedcomRecord``, как её отдал ``parse_records()`` ДО
            свёртки в document. Пусто (``()``) — caller не передал
            (CLI / unit-тест без raw-доступа).
    """

    raw_records: tuple[GedcomRecord, ...] = ()


__all__ = ["Finding", "Severity", "ValidatorContext"]
