"""Pydantic-модели и загрузчик YAML-правил для Compatibility Simulator.

YAML-файлы целевых платформ лежат в этой же директории
(``ancestry.yaml``, ``myheritage.yaml``, ``familysearch.yaml``,
``gramps.yaml``) и грузятся через :func:`load_rules` как package data.

Минимальный пример формата:

.. code-block:: yaml

    target: ancestry
    description: "Ancestry.com web platform behavior on GEDCOM import"
    drops:
      - tag: _UID
        reason: "Ancestry overwrites custom _UID with its own internal id"
        weight: 0.02
      - feature: name_variants
        reason: "FONE/ROMN name variants stripped on import"
        weight: 0.05
    encoding:
      max_charset: ASCII
      substitutions:
        "ё": "е"
        "—": "-"
      weight: 0.04
    structure:
      - description: "Note-level citations are flattened to person-level"
        severity: 0.20
        trigger: any_event_citations

Drop rules могут быть двух видов:

* ``tag``: совпадает с проприетарным/неизвестным тегом, который
  semantic-слой quarantine'ил в ``GedcomDocument.unknown_tags`` (Phase 5.5a).
  Поддерживаются три формы:

  - ``"_UID"`` — match по точному имени тега (любой owner_kind).
  - ``"INDI._UID"`` — match по паре ``<top-level-tag>.<child-tag>``,
    где левая часть — имя записи (``INDI``/``FAM``/``SOUR``/``NOTE``/
    ``OBJE``/``REPO``/``SUBM``/``HEAD``).
  - ``"_*"`` — wildcard prefix (любой подчёркнутый proprietary-тег).

* ``feature``: именованный «структурный» хук, который смотрит на known-but-
  target-incompatible части документа (FONE/ROMN-варианты имён, inline-OBJE,
  per-event citations и т.п.). Список — :data:`FEATURE_NAMES`.

Encoding-правила фильтруют любые string-поля entities. ``substitutions``
применяются ДО проверки ``max_charset``; всё, что не попало в charset
после подстановок, заменяется на ``?`` (как делает большинство
импортёров с unsupported-символами).

Structure-правила emit'ятся, если их ``trigger`` совпадает с состоянием
документа. Это «бесплатные предупреждения», которые не привязаны к
конкретной записи — для UI это «общие риски импорта».
"""

from __future__ import annotations

from importlib.resources import files
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Все известные «фичи», на которые могут ссылаться drop-правила. Соответствие
#: реальному поведению — смотри :func:`gedcom_parser.compatibility.simulator._yield_feature_hits`.
FEATURE_NAMES: Final[frozenset[str]] = frozenset(
    {
        # Person.names[*].variants — FONE/ROMN альтернативные написания.
        "name_variants",
        # Person/Family.inline_objects — inline-OBJE без xref.
        "inline_objects",
        # Person/Family events with non-empty citations (per-event sources).
        "event_citations",
        # Citations внутри events с inline-NOTE.
        "citation_inline_notes",
        # Source.text, Source.publication — длинный произвольный текст.
        "source_long_text",
        # Person с >1 NAME — нестандартные альтернативные имена.
        "multiple_names",
    }
)

#: Допустимые charset'ы в encoding-правиле. Семантика: «всё, что внутри —
#: passthrough; всё, что снаружи — после substitutions заменяется на ?».
MaxCharset = Literal["ASCII", "Latin-1", "UTF-8"]

#: Допустимые триггеры для structure-правила. См. описание в модуле.
StructureTrigger = Literal[
    "always",
    "any_inline_obje",
    "any_event_citations",
    "any_name_variants",
    "any_multiple_names",
    "any_source_text",
]


class DropRule(BaseModel):
    """Одно drop-правило: «таргет дропнет такие-то теги/фичи на import».

    Ровно одно из ``tag`` / ``feature`` должно быть задано.
    """

    tag: str | None = Field(
        default=None,
        description=(
            "Имя тега для match'а против RawTagBlock в GedcomDocument.unknown_tags. "
            "Формы: '_UID' (любой owner), 'INDI._UID' (qualified), '_*' (wildcard prefix)."
        ),
    )
    feature: str | None = Field(
        default=None,
        description=(
            "Именованный structural-хук (см. FEATURE_NAMES). "
            "Match'ит known-but-target-incompatible части документа."
        ),
    )
    reason: str = Field(min_length=1, description="Человекочитаемая причина для UI.")
    weight: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Вклад одного срабатывания в estimated_loss_pct.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def _exactly_one_selector(self) -> DropRule:
        if (self.tag is None) == (self.feature is None):
            msg = "DropRule requires exactly one of `tag` or `feature` to be set"
            raise ValueError(msg)
        if self.feature is not None and self.feature not in FEATURE_NAMES:
            msg = f"unknown feature {self.feature!r}, expected one of: {sorted(FEATURE_NAMES)}"
            raise ValueError(msg)
        return self


class EncodingRule(BaseModel):
    """Правило кодировки: что таргет умеет принимать в string-полях."""

    max_charset: MaxCharset = Field(
        default="UTF-8",
        description=(
            "Самый широкий charset, который таргет принимает без потерь. "
            "ASCII = только 0x00-0x7F; Latin-1 = 0x00-0xFF; UTF-8 = всё."
        ),
    )
    substitutions: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Прямые замены символ→символ, применяемые ДО проверки max_charset. "
            "Например, {'ё': 'е', '—': '-'} для целей с ASCII-only."
        ),
    )
    weight: float = Field(
        default=0.03,
        ge=0.0,
        le=1.0,
        description="Вклад одного encoding-warning в estimated_loss_pct.",
    )

    model_config = ConfigDict(frozen=True, extra="forbid")


class StructureRule(BaseModel):
    """Описание структурного риска импорта (например, flattening notes).

    В отличие от DropRule, не привязано к конкретной записи: это общее
    предупреждение для всего файла. Срабатывает по триггеру.
    """

    description: str = Field(min_length=1)
    severity: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Серьёзность (0..1). Влияет на estimated_loss_pct.",
    )
    trigger: StructureTrigger = "always"

    model_config = ConfigDict(frozen=True, extra="forbid")


class TargetRules(BaseModel):
    """Полный набор правил для одной целевой платформы."""

    target: str = Field(min_length=1)
    description: str = ""
    drops: tuple[DropRule, ...] = ()
    encoding: EncodingRule | None = None
    structure: tuple[StructureRule, ...] = ()

    model_config = ConfigDict(frozen=True, extra="forbid")


def load_rules(target: str) -> TargetRules:
    """Загрузить YAML-правила для ``target`` из package data.

    Args:
        target: Имя таргет-платформы. Файл должен существовать как
            ``compatibility/rules/<target>.yaml``.

    Raises:
        FileNotFoundError: Если YAML для таргета не существует.
        ValueError: При невалидном YAML или невалидной модели TargetRules.
    """
    resource = files("gedcom_parser.compatibility.rules").joinpath(f"{target}.yaml")
    if not resource.is_file():
        msg = f"no compatibility rules bundled for target {target!r}"
        raise FileNotFoundError(msg)
    with resource.open("rb") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        msg = f"compatibility rules for {target!r} must be a YAML mapping at top level"
        raise ValueError(msg)
    return TargetRules.model_validate(data)


__all__ = [
    "FEATURE_NAMES",
    "DropRule",
    "EncodingRule",
    "MaxCharset",
    "StructureRule",
    "StructureTrigger",
    "TargetRules",
    "load_rules",
]
