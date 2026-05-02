"""Деterministic narrative builder для Relationship Research Report.

Строит 4–8 параграфов связного текста по claim_type + персонам + evidence.
Никакого LLM: одна и та же входная пара даёт побитово идентичный output
(snapshot-friendly).

Шаблоны фраз — простые f-string'и, разные для en и ru. Вход — уже
агрегированный context, builder ничего не читает из БД.
"""

from __future__ import annotations

from report_service.relationship.locale import claim_label
from report_service.relationship.models import (
    ClaimedRelationship,
    EvidencePiece,
    PersonSummary,
    ReportLocale,
    is_direct_claim,
)


def build_narrative(
    *,
    person_a: PersonSummary,
    person_b: PersonSummary,
    claim: ClaimedRelationship,
    evidence: list[EvidencePiece],
    counter_evidence: list[EvidencePiece],
    direct_relationship_resolved: bool,
    locale: ReportLocale,
) -> str:
    """Собрать итоговый narrative-параграф (4–8 секций, разделённых ``\\n\\n``).

    Детерминирован: одинаковые входы → одинаковый текст. Используется
    snapshot-тестами в `tests/test_relationship_render_unit.py`.
    """
    paragraphs: list[str] = []

    paragraphs.append(_intro(person_a, person_b, claim, locale))

    if is_direct_claim(claim):
        paragraphs.append(_direct_resolution(direct_relationship_resolved, claim, locale))
    else:
        paragraphs.append(_extended_caveat(claim, locale))

    paragraphs.append(_evidence_summary(evidence, counter_evidence, locale))

    if evidence:
        paragraphs.append(_supporting_breakdown(evidence, locale))
    if counter_evidence:
        paragraphs.append(_counter_breakdown(counter_evidence, locale))

    paragraphs.append(_closing(person_a, person_b, claim, locale))

    return "\n\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _intro(
    a: PersonSummary,
    b: PersonSummary,
    claim: ClaimedRelationship,
    locale: ReportLocale,
) -> str:
    label = claim_label(claim, locale)
    if locale == "ru":
        return (
            f"Этот отчёт исследует утверждение, что {a.primary_name} ({_lifespan(a, locale)}) "
            f"и {b.primary_name} ({_lifespan(b, locale)}) — {label}."
        )
    return (
        f"This report examines the claim that {a.primary_name} ({_lifespan(a, locale)}) "
        f"and {b.primary_name} ({_lifespan(b, locale)}) are {label}."
    )


def _direct_resolution(
    resolved: bool,
    claim: ClaimedRelationship,
    locale: ReportLocale,
) -> str:
    label = claim_label(claim, locale)
    if resolved:
        if locale == "ru":
            return (
                f"Связь типа «{label}» подтверждается записями Family/FamilyChild "
                f"в дереве. Ниже приведены доказательства, относящиеся к этой связи."
            )
        return (
            f"The {label} relationship is confirmed by Family/FamilyChild records "
            f"in the tree. The evidence supporting this relationship is detailed below."
        )
    if locale == "ru":
        return (
            f"Связь типа «{label}» НЕ найдена в Family/FamilyChild дерева. "
            f"Это означает, что либо связывающая запись отсутствует, либо "
            f"утверждение неверно — рекомендуется ручная проверка."
        )
    return (
        f"The {label} relationship is NOT present in the tree's Family/FamilyChild "
        f"records. This means either the linking row is missing or the claim is "
        f"incorrect; manual verification is required."
    )


def _extended_caveat(claim: ClaimedRelationship, locale: ReportLocale) -> str:
    label = claim_label(claim, locale)
    if locale == "ru":
        return (
            f"«{label}» — дальняя связь. Phase 24.3 v1 не агрегирует evidence "
            f"через промежуточные поколения; ниже показаны только прямые "
            f"DNA-совпадения и off-catalog evidence, привязанные к данной паре."
        )
    return (
        f"The {label} claim is an extended-distance relationship. Phase 24.3 v1 "
        f"does not aggregate evidence through intermediate generations; the section "
        f"below shows only direct DNA matches and off-catalog evidence attached "
        f"to this pair."
    )


def _evidence_summary(
    supporting: list[EvidencePiece],
    contradicting: list[EvidencePiece],
    locale: ReportLocale,
) -> str:
    sup_n, contra_n = len(supporting), len(contradicting)
    if locale == "ru":
        return f"Найдено {sup_n} подтверждающих и {contra_n} опровергающих единиц доказательств."
    return (
        f"{sup_n} supporting and {contra_n} contradicting evidence "
        f"piece{'s' if sup_n + contra_n != 1 else ''} found."
    )


def _supporting_breakdown(pieces: list[EvidencePiece], locale: ReportLocale) -> str:
    by_kind: dict[str, int] = {}
    for p in pieces:
        by_kind[p.kind] = by_kind.get(p.kind, 0) + 1
    parts = sorted(by_kind.items())
    breakdown = ", ".join(f"{kind}={n}" for kind, n in parts)
    if locale == "ru":
        return f"Распределение подтверждающих по типам: {breakdown}."
    return f"Supporting evidence by kind: {breakdown}."


def _counter_breakdown(pieces: list[EvidencePiece], locale: ReportLocale) -> str:
    by_kind: dict[str, int] = {}
    for p in pieces:
        by_kind[p.kind] = by_kind.get(p.kind, 0) + 1
    parts = sorted(by_kind.items())
    breakdown = ", ".join(f"{kind}={n}" for kind, n in parts)
    if locale == "ru":
        return (
            f"Опровергающие доказательства присутствуют — распределение по типам: "
            f"{breakdown}. Это снижает композитный confidence отчёта."
        )
    return (
        f"Contradicting evidence is present — by kind: {breakdown}. "
        f"This reduces the report's composite confidence."
    )


def _closing(
    a: PersonSummary,
    b: PersonSummary,
    claim: ClaimedRelationship,
    locale: ReportLocale,
) -> str:
    label = claim_label(claim, locale)
    if locale == "ru":
        return (
            f"Композитный confidence по формуле Phase 22.5 (см. секцию «Расчёт "
            f"достоверности» ниже) — основа решения о статусе утверждения "
            f"«{a.primary_name} и {b.primary_name} — {label}». Используйте отчёт "
            f"в связке с DNA-совпадениями и архивными запросами для финальной верификации."
        )
    return (
        f"The composite confidence (Phase 22.5 weighted aggregation, see "
        f'"Confidence calculation" section below) is the basis for accepting '
        f"or rejecting the claim that {a.primary_name} and {b.primary_name} "
        f"are {label}. Use this report alongside DNA matches and archive "
        f"requests for final verification."
    )


def _lifespan(person: PersonSummary, locale: ReportLocale) -> str:
    if person.birth_year is None and person.death_year is None:
        return "—" if locale == "ru" else "dates unknown"
    b = str(person.birth_year) if person.birth_year is not None else "?"
    d = str(person.death_year) if person.death_year is not None else "?"
    return f"{b}–{d}"


__all__ = ["build_narrative"]
