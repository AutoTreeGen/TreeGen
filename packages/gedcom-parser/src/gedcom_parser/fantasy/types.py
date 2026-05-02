"""Public types for fantasy filter (Phase 5.10).

Mirror :mod:`gedcom_parser.validator.types` shape, но 4 severity-уровня
вместо 3 (info / warning / high / critical) и доп. поля для confidence
+ structured ``evidence`` payload.

Дизайн-нота: dataclass frozen + JSON-сериализуемые поля. Persisted в
``fantasy_flags`` row (ADR-0077). Round-trip: ``FantasyFlag.from_dict(
f.to_dict()) == f``.

**confidence** — float [0, 1]. ADR-0077 фиксирует cap=0.95 даже на
critical-rules: оставляем room для legit edge-cases (very long-lived
documented ancestors, immigrant date gaps).
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class FantasySeverity(enum.StrEnum):
    """Severity levels (4 уровня, отличается от validator's 3).

    - ``INFO``: «странно, но скорее всего ОК».
    - ``WARNING``: подозрительно, человек должен взглянуть.
    - ``HIGH``: вероятно ошибка / fabrication.
    - ``CRITICAL``: логически невозможно (death before birth, циклы).
    """

    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


# ADR-0077 cap: даже critical-rule не выставляет confidence выше этого.
# Оставляем 5%-luft для legit-but-unusual cases (верифицированный
# 122-летний человек, переселенцы с пропусками в paperwork).
MAX_CONFIDENCE = 0.95


@dataclass(frozen=True, slots=True)
class FantasyFlag:
    """Один advisory flag, выпущенный одним правилом.

    Attributes:
        rule_id: Стабильный идентификатор правила (snake_case). UI и
            analytics группируют по нему. Меняется только через
            breaking-change ADR.
        severity: :class:`FantasySeverity` уровень.
        confidence: ∈ [0, MAX_CONFIDENCE]. Внутренние правила clamp'ят.
        reason: Человекочитаемое описание (English; UI-локализация —
            future). Должно содержать конкретные значения (years, ages),
            но не PII полностью.
        person_xref: GEDCOM xref персоны-субъекта или None.
        family_xref: GEDCOM xref семьи-субъекта или None. Один flag
            может иметь оба (e.g. «child birth after father death»
            привязан и к ребёнку, и к семье через отца).
        evidence: Структурированный JSON-payload для UI / debug. Должно
            быть JSON-сериализуемо (числа / строки / bool / list / dict).
        suggested_action: Краткий совет UI (e.g. «verify mother's death
            date or remove this child from this family»). None — нет
            универсального рецепта.
    """

    rule_id: str
    severity: FantasySeverity
    confidence: float
    reason: str
    person_xref: str | None = None
    family_xref: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-сериализуемое представление (для persist в jsonb)."""
        data = asdict(self)
        data["severity"] = self.severity.value
        return data


@dataclass(frozen=True, slots=True)
class FantasyContext:
    """Опциональный per-scan context (config overrides, thresholds).

    На v1 пустой, оставлен for forward-compat: будущие правила смогут
    читать tunable thresholds (e.g. mass_fabricated_branch min_size,
    direct_descent anchor whitelist) без breaking-change rule API.
    """

    enabled_rules: frozenset[str] | None = None
    """Whitelist rule_id'ов; None — все default-enabled. Используется в
    POST /fantasy-scan ``rules`` параметре."""


__all__ = ["MAX_CONFIDENCE", "FantasyContext", "FantasyFlag", "FantasySeverity"]
