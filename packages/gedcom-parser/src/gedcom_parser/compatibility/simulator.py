"""Compatibility Simulator: предсказание потерь при импорте в целевую платформу.

Главный входной API:

    simulate(doc, target) -> CompatibilityReport

См. модуль ``compatibility.rules`` для модели правил и ``compatibility/rules/``
для YAML-конфигов целевых платформ.

Алгоритм:

1. **Tag drops.**
   * Для каждого ``RawTagBlock`` из ``doc.unknown_tags`` ищется match
     против ``rules.drops`` с заполненным полем ``tag``. Поддерживаются
     точное, qualified (``INDI._UID``) и wildcard-prefix (``_*``) match'и.
   * Параллельно прогоняется список ``feature``-правил: они смотрят на
     известные части AST (FONE/ROMN, inline-OBJE, per-event citations,
     множественные NAME, длинный SOUR.TEXT).

2. **Encoding warnings.** Каждое строковое поле всех известных entity'ev
   прогоняется через ``substitutions``, затем проверяется ``max_charset``.
   Любые символы, не попавшие в charset после подстановок, заменяются на
   ``?`` — и emit'ится :class:`EncodingIssue` с парой (original, will_become).

3. **Structure changes.** Triggers оцениваются по агрегированным
   признакам документа (есть ли хоть одно событие с citations, хоть один
   inline OBJE и т.д.). Если триггер активен — emit'ится structure-warning.

4. **Estimated loss %.** Сумма весов всех найденных проблем, нормированная
   по числу персон+семей+источников+заметок+медиа+репозиториев+отправителей
   (минимум 1, чтобы не делить на ноль), и зажатая в [0.0, 1.0].
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from gedcom_parser.compatibility.rules import (
    DropRule,
    EncodingRule,
    StructureRule,
    StructureTrigger,
    TargetRules,
    load_rules,
)
from gedcom_parser.document import GedcomDocument
from gedcom_parser.entities import Event

#: Литерал поддерживаемых таргет-платформ (для type-checking call-sites).
Target = Literal["ancestry", "myheritage", "familysearch", "gramps"]

#: Кортеж тех же таргетов в runtime-форме (для тестов и iteration).
TARGETS: Final[tuple[Target, ...]] = ("ancestry", "myheritage", "familysearch", "gramps")

#: ``RawTagBlock.owner_kind`` → синтаксис верхнеуровневого тега, который
#: пользователь записывает в ``rules.drops[*].tag`` как ``"INDI._UID"``.
_OWNER_KIND_TO_TOP_TAG: Final[dict[str, str]] = {
    "individual": "INDI",
    "family": "FAM",
    "source": "SOUR",
    "note": "NOTE",
    "object": "OBJE",
    "repository": "REPO",
    "submitter": "SUBM",
    "header": "HEAD",
    "custom": "_CUSTOM",
}


# -----------------------------------------------------------------------------
# Модели отчёта
# -----------------------------------------------------------------------------


class TagDrop(BaseModel):
    """Одна потеря тега / фичи при импорте в таргет."""

    xref: str = Field(
        description=(
            "xref записи-источника (``I1``, ``F2``, ``S3`` …). "
            "``HEAD`` — для тегов в header'е. ``_CUSTOM`` — для редких "
            "feature-drops, не привязанных к конкретной записи."
        )
    )
    tag_path: str = Field(
        description=(
            "Quad: ``<TOP>.<TAG>`` для quarantined тегов; ``feature:<name>`` для feature-drops."
        )
    )
    reason: str

    model_config = ConfigDict(frozen=True)


class EncodingIssue(BaseModel):
    """Один символ/строка, которые таргет получит искажёнными."""

    xref: str
    field: str = Field(
        description=(
            "Имя поля, в котором обнаружена проблема. "
            "Например, ``NAME[0].value``, ``BIRT.PLAC``, ``SOUR.TITL``."
        )
    )
    original: str
    will_become: str

    model_config = ConfigDict(frozen=True)


class StructureChange(BaseModel):
    """Общий структурный риск, не привязанный к конкретной записи."""

    description: str
    severity: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(frozen=True)


class CompatibilityReport(BaseModel):
    """Полный отчёт симулятора для одной пары (doc, target)."""

    target: str
    tag_drops: tuple[TagDrop, ...] = ()
    encoding_warnings: tuple[EncodingIssue, ...] = ()
    structure_changes: tuple[StructureChange, ...] = ()
    estimated_loss_pct: float = Field(
        ge=0.0,
        le=1.0,
        description="Оценка относительной потери информации, [0.0, 1.0].",
    )

    model_config = ConfigDict(frozen=True)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def simulate(doc: GedcomDocument, target: Target) -> CompatibilityReport:
    """Прогнать ``doc`` через правила ``target`` и вернуть отчёт о потерях."""
    rules = load_rules(target)
    weighted_drops = _collect_tag_drops(doc, rules)
    drops = tuple(d for d, _ in weighted_drops)
    enc_warnings = _collect_encoding_warnings(doc, rules.encoding)
    struct_changes = _collect_structure_changes(doc, rules.structure)
    loss = _estimate_loss_pct(doc, rules, weighted_drops, enc_warnings, struct_changes)
    return CompatibilityReport(
        target=rules.target,
        tag_drops=drops,
        encoding_warnings=enc_warnings,
        structure_changes=struct_changes,
        estimated_loss_pct=loss,
    )


# -----------------------------------------------------------------------------
# Tag drops: quarantined-tag match + feature-hooks
# -----------------------------------------------------------------------------


def _collect_tag_drops(
    doc: GedcomDocument, rules: TargetRules
) -> tuple[tuple[TagDrop, float], ...]:
    """Собрать tag-drops: quarantined tag rules + feature-rules.

    Возвращает пары ``(TagDrop, rule_weight)`` — public-side TagDrop'ы
    оставляют 3 поля (xref, tag_path, reason), но вес matched-правила
    нужен для подсчёта ``estimated_loss_pct``.
    """
    out: list[tuple[TagDrop, float]] = []
    out.extend(_iter_quarantined_drops(doc, rules.drops))
    out.extend(_iter_feature_drops(doc, rules.drops))
    return tuple(out)


def _iter_quarantined_drops(
    doc: GedcomDocument, drops: tuple[DropRule, ...]
) -> list[tuple[TagDrop, float]]:
    """Tag-drops, основанные на ``GedcomDocument.unknown_tags``.

    Каждое drop-правило с непустым ``tag`` сравнивается с tag'ом каждого
    quarantined-блока. Поддержка трёх форм паттерна:

    * ``"_UID"`` — match если ``block.record.tag == "_UID"`` (любой owner).
    * ``"INDI._UID"`` — match только если block записан в INDI и tag совпал.
    * ``"_*"`` — match любого тега, начинающегося с ``"_"``.
    """
    out: list[tuple[TagDrop, float]] = []
    tag_rules = [r for r in drops if r.tag is not None]
    for block in doc.unknown_tags:
        block_tag = block.record.tag
        owner_top = _OWNER_KIND_TO_TOP_TAG.get(block.owner_kind, "?")
        qualified = f"{owner_top}.{block_tag}"
        rule = _match_tag_rule(block_tag, owner_top, tag_rules)
        if rule is None:
            continue
        out.append(
            (
                TagDrop(
                    xref=block.owner_xref_id,
                    tag_path=qualified,
                    reason=rule.reason,
                ),
                rule.weight,
            )
        )
    return out


def _match_tag_rule(block_tag: str, owner_top: str, tag_rules: list[DropRule]) -> DropRule | None:
    """Найти первое правило, чей ``tag`` подходит block'у. Иначе ``None``.

    Приоритет: qualified > exact > wildcard. Это даёт user'у возможность
    написать общий ``_UID`` (drop везде), но переопределить ``HEAD._UID``
    как preserved (если когда-нибудь понадобится — пока «исключений» нет).
    """
    qualified_match: DropRule | None = None
    exact_match: DropRule | None = None
    wildcard_match: DropRule | None = None
    for rule in tag_rules:
        pattern = rule.tag
        if pattern is None:
            continue
        if "." in pattern:
            top, _, child = pattern.partition(".")
            if top == owner_top and child == block_tag and qualified_match is None:
                qualified_match = rule
        elif pattern.endswith("*"):
            prefix = pattern[:-1]
            if block_tag.startswith(prefix) and wildcard_match is None:
                wildcard_match = rule
        elif pattern == block_tag and exact_match is None:
            exact_match = rule
    return qualified_match or exact_match or wildcard_match


def _iter_feature_drops(
    doc: GedcomDocument, drops: tuple[DropRule, ...]
) -> list[tuple[TagDrop, float]]:
    """Tag-drops, основанные на feature-хуках (known-but-incompatible)."""
    out: list[tuple[TagDrop, float]] = []
    for rule in drops:
        if rule.feature is None:
            continue
        for xref, label in _yield_feature_hits(doc, rule.feature):
            out.append(
                (
                    TagDrop(xref=xref, tag_path=f"feature:{label}", reason=rule.reason),
                    rule.weight,
                )
            )
    return out


def _yield_feature_hits(doc: GedcomDocument, feature: str) -> list[tuple[str, str]]:
    """Перечислить (xref, label) — каждое совпадение указанного feature.

    label — короткий человекочитаемый ярлык, попадающий в ``tag_path`` после
    префикса ``feature:`` (например, ``"name_variants"``, ``"inline_obje"``).
    """
    if feature == "name_variants":
        return _hits_name_variants(doc)
    if feature == "inline_objects":
        return _hits_inline_objects(doc)
    if feature == "event_citations":
        return _hits_event_citations(doc)
    if feature == "citation_inline_notes":
        return _hits_citation_inline_notes(doc)
    if feature == "source_long_text":
        return _hits_source_long_text(doc)
    if feature == "multiple_names":
        return _hits_multiple_names(doc)
    return []


def _hits_name_variants(doc: GedcomDocument) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for person in doc.persons.values():
        for name in person.names:
            for variant in name.variants:
                out.append((person.xref_id, f"name_variants:{variant.kind}"))
    return out


def _hits_inline_objects(doc: GedcomDocument) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for person in doc.persons.values():
        out.extend((person.xref_id, "inline_objects") for _ in person.inline_objects)
    for family in doc.families.values():
        out.extend((family.xref_id, "inline_objects") for _ in family.inline_objects)
    return out


def _hits_event_citations(doc: GedcomDocument) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for person in doc.persons.values():
        for event in person.events:
            if event.citations:
                out.append((person.xref_id, f"event_citations:{event.tag}"))
    for family in doc.families.values():
        for event in family.events:
            if event.citations:
                out.append((family.xref_id, f"event_citations:{event.tag}"))
    return out


def _hits_citation_inline_notes(doc: GedcomDocument) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for person in doc.persons.values():
        for citation in person.citations:
            if citation.notes_inline:
                out.append((person.xref_id, "citation_inline_notes"))
        for event in person.events:
            for citation in event.citations:
                if citation.notes_inline:
                    out.append((person.xref_id, f"citation_inline_notes:{event.tag}"))
    for family in doc.families.values():
        for citation in family.citations:
            if citation.notes_inline:
                out.append((family.xref_id, "citation_inline_notes"))
    return out


_LONG_TEXT_THRESHOLD: Final[int] = 248


def _hits_source_long_text(doc: GedcomDocument) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for source in doc.sources.values():
        if source.text is not None and len(source.text) > _LONG_TEXT_THRESHOLD:
            out.append((source.xref_id, "source_long_text:TEXT"))
        if source.publication is not None and len(source.publication) > _LONG_TEXT_THRESHOLD:
            out.append((source.xref_id, "source_long_text:PUBL"))
    return out


def _hits_multiple_names(doc: GedcomDocument) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for person in doc.persons.values():
        if len(person.names) > 1:
            for idx in range(1, len(person.names)):
                out.append((person.xref_id, f"multiple_names:NAME[{idx}]"))
    return out


# -----------------------------------------------------------------------------
# Encoding warnings
# -----------------------------------------------------------------------------


def _charset_max_codepoint(charset: str) -> int:
    """Максимальный codepoint, который таргет принимает без потерь."""
    if charset == "ASCII":
        return 0x7F
    if charset == "Latin-1":
        return 0xFF
    return 0x10FFFF


def _apply_encoding(value: str, rule: EncodingRule) -> str:
    """Применить substitutions, потом заменить «лишнее» на ``?``."""
    out = value
    for src, dst in rule.substitutions.items():
        if src in out:
            out = out.replace(src, dst)
    limit = _charset_max_codepoint(rule.max_charset)
    if all(ord(ch) <= limit for ch in out):
        return out
    return "".join(ch if ord(ch) <= limit else "?" for ch in out)


def _collect_encoding_warnings(
    doc: GedcomDocument, rule: EncodingRule | None
) -> tuple[EncodingIssue, ...]:
    """Перебрать все строковые поля и собрать те, что мутируют."""
    if rule is None:
        return ()
    out: list[EncodingIssue] = []

    def add(xref: str, label: str, value: str | None) -> None:
        if value is None:
            return
        issue = _check_field(xref, label, value, rule)
        if issue is not None:
            out.append(issue)

    for person in doc.persons.values():
        for idx, name in enumerate(person.names):
            add(person.xref_id, f"NAME[{idx}].value", name.value)
            add(person.xref_id, f"NAME[{idx}].GIVN", name.given)
            add(person.xref_id, f"NAME[{idx}].SURN", name.surname)
            add(person.xref_id, f"NAME[{idx}].NICK", name.nickname)
        for event in person.events:
            _scan_event_strings(person.xref_id, event, add)
    for family in doc.families.values():
        for event in family.events:
            _scan_event_strings(family.xref_id, event, add)
    for source in doc.sources.values():
        add(source.xref_id, "SOUR.TITL", source.title)
        add(source.xref_id, "SOUR.AUTH", source.author)
        add(source.xref_id, "SOUR.ABBR", source.abbreviation)
        add(source.xref_id, "SOUR.PUBL", source.publication)
        add(source.xref_id, "SOUR.TEXT", source.text)
    for note in doc.notes.values():
        add(note.xref_id, "NOTE", note.text)
    for obje in doc.objects.values():
        add(obje.xref_id, "OBJE.FILE", obje.file)
        add(obje.xref_id, "OBJE.TITL", obje.title)
        add(obje.xref_id, "OBJE.TYPE", obje.type_)
    for repo in doc.repositories.values():
        add(repo.xref_id, "REPO.NAME", repo.name)
        add(repo.xref_id, "REPO.ADDR", repo.address_raw)
    for subm in doc.submitters.values():
        add(subm.xref_id, "SUBM.NAME", subm.name)
    return tuple(out)


def _scan_event_strings(
    xref: str,
    event: Event,
    add: Callable[[str, str, str | None], None],
) -> None:
    add(xref, f"{event.tag}.DATE", event.date_raw)
    add(xref, f"{event.tag}.PLAC", event.place_raw)
    add(xref, f"{event.tag}.TYPE", event.type_)
    add(xref, f"{event.tag}.AGE", event.age_raw)


def _check_field(xref: str, label: str, value: str, rule: EncodingRule) -> EncodingIssue | None:
    """Если applied != original — вернуть один EncodingIssue, иначе ``None``."""
    transformed = _apply_encoding(value, rule)
    if transformed == value:
        return None
    return EncodingIssue(xref=xref, field=label, original=value, will_become=transformed)


# -----------------------------------------------------------------------------
# Structure changes
# -----------------------------------------------------------------------------


def _collect_structure_changes(
    doc: GedcomDocument, rules: tuple[StructureRule, ...]
) -> tuple[StructureChange, ...]:
    triggers = _evaluate_triggers(doc)
    out: list[StructureChange] = []
    for rule in rules:
        if triggers.get(rule.trigger, False):
            out.append(StructureChange(description=rule.description, severity=rule.severity))
    return tuple(out)


def _evaluate_triggers(doc: GedcomDocument) -> dict[StructureTrigger, bool]:
    """Посчитать значения всех известных триггеров для ``doc``."""
    has_inline_obje = any(p.inline_objects for p in doc.persons.values()) or any(
        f.inline_objects for f in doc.families.values()
    )
    has_event_citations = any(
        any(e.citations for e in p.events) for p in doc.persons.values()
    ) or any(any(e.citations for e in f.events) for f in doc.families.values())
    has_name_variants = any(any(n.variants for n in p.names) for p in doc.persons.values())
    has_multiple_names = any(len(p.names) > 1 for p in doc.persons.values())
    has_source_text = any(s.text is not None for s in doc.sources.values())
    return {
        "always": True,
        "any_inline_obje": has_inline_obje,
        "any_event_citations": has_event_citations,
        "any_name_variants": has_name_variants,
        "any_multiple_names": has_multiple_names,
        "any_source_text": has_source_text,
    }


# -----------------------------------------------------------------------------
# Loss estimation
# -----------------------------------------------------------------------------


def _estimate_loss_pct(
    doc: GedcomDocument,
    rules: TargetRules,
    weighted_drops: tuple[tuple[TagDrop, float], ...],
    enc_warnings: tuple[EncodingIssue, ...],
    struct_changes: tuple[StructureChange, ...],
) -> float:
    """Сложить веса всех найденных проблем, нормировать по числу записей.

    Знаменатель = ``max(1, total_entities)`` — чтобы маленький файл не
    получал «100 % потерь» от двух предупреждений. Числитель — сумма:

    * для каждого ``TagDrop`` — вес matched-правила;
    * для каждого ``EncodingIssue`` — ``rules.encoding.weight`` (или `0.03`);
    * для каждого ``StructureChange`` — его ``severity``.

    Финальный score зажимается в [0, 1].
    """
    total_entities = max(
        1,
        len(doc.persons)
        + len(doc.families)
        + len(doc.sources)
        + len(doc.notes)
        + len(doc.objects)
        + len(doc.repositories)
        + len(doc.submitters),
    )
    drop_weight = sum(weight for _, weight in weighted_drops)
    enc_per_issue = rules.encoding.weight if rules.encoding is not None else 0.03
    enc_weight = enc_per_issue * len(enc_warnings)
    struct_weight = sum(s.severity for s in struct_changes)
    raw = (drop_weight + enc_weight + struct_weight) / float(total_entities)
    return max(0.0, min(1.0, raw))


__all__ = [
    "TARGETS",
    "CompatibilityReport",
    "EncodingIssue",
    "StructureChange",
    "TagDrop",
    "Target",
    "simulate",
]
