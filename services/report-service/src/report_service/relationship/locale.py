"""i18n строки для Relationship Research Report (en + ru).

Hardcoded mapping вместо подключения next-intl / babel — отчёт это
backend-render, не shared с frontend bundle. Расширение на новые
locale = новая запись в ``_STRINGS``.

Ключи зеркалят (где это уместно) ``parser_service.court_ready.locale``,
чтобы UI-страница «Generate Report» могла показывать одни и те же
labels для обоих типов отчётов без duplication.
"""

from __future__ import annotations

from typing import Final

from report_service.relationship.models import ClaimedRelationship, ReportLocale

_CLAIM_LABELS: Final[dict[ClaimedRelationship, dict[ReportLocale, str]]] = {
    ClaimedRelationship.PARENT_CHILD: {"en": "parent and child", "ru": "родитель и ребёнок"},
    ClaimedRelationship.SIBLING: {"en": "siblings", "ru": "брат/сестра"},
    ClaimedRelationship.GRANDPARENT_GRANDCHILD: {
        "en": "grandparent and grandchild",
        "ru": "дед/бабушка и внук(чка)",
    },
    ClaimedRelationship.AUNT_UNCLE_NIECE_NEPHEW: {
        "en": "aunt/uncle and niece/nephew",
        "ru": "тётя/дядя и племянник(ца)",
    },
    ClaimedRelationship.FIRST_COUSIN: {"en": "first cousins", "ru": "двоюродные"},
    ClaimedRelationship.SECOND_COUSIN: {"en": "second cousins", "ru": "троюродные"},
    ClaimedRelationship.THIRD_COUSIN: {"en": "third cousins", "ru": "четвероюродные"},
    ClaimedRelationship.FOURTH_PLUS_COUSIN: {
        "en": "fourth-or-more-distant cousins",
        "ru": "пятиюродные и более дальние",
    },
    ClaimedRelationship.SPOUSE: {"en": "spouses", "ru": "супруги"},
    ClaimedRelationship.OTHER: {"en": "other relationship", "ru": "другая связь"},
}


_STRINGS: Final[dict[str, dict[ReportLocale, str]]] = {
    "report_title_formal": {
        "en": "Relationship Research Report",
        "ru": "Исследовательский отчёт по связи",
    },
    "report_title_client": {
        "en": "Family connection — research report",
        "ru": "Семейная связь — исследовательский отчёт",
    },
    "subtitle_template": {
        "en": "Evidence supporting the claim that {a} and {b} are {claim_label}.",
        "ru": "Доказательства того, что {a} и {b} — {claim_label}.",
    },
    "claim": {"en": "Claim", "ru": "Утверждение"},
    "tree": {"en": "Tree", "ru": "Дерево"},
    "person_a": {"en": "Person A", "ru": "Персона A"},
    "person_b": {"en": "Person B", "ru": "Персона B"},
    "primary_name": {"en": "Primary name", "ru": "Основное имя"},
    "also_known_as": {"en": "Also known as", "ru": "Также известен как"},
    "sex": {"en": "Sex", "ru": "Пол"},
    "lifespan": {"en": "Lifespan", "ru": "Годы жизни"},
    "narrative": {"en": "Narrative", "ru": "Изложение"},
    "evidence_summary": {"en": "Evidence summary", "ru": "Сводка доказательств"},
    "supporting_evidence": {"en": "Supporting evidence", "ru": "Подтверждающие доказательства"},
    "counter_evidence": {"en": "Counter-evidence", "ru": "Опровергающие доказательства"},
    "no_supporting": {
        "en": (
            "No supporting evidence is recorded for this claim in the tree. "
            "The narrative below describes the structural placement of the "
            "two persons; further verification is recommended."
        ),
        "ru": (
            "В дереве не зафиксированы доказательства, подкрепляющие это "
            "утверждение. Изложение ниже описывает структурное расположение "
            "двух персон; рекомендуется дополнительная верификация."
        ),
    },
    "no_counter": {
        "en": "No contradicting evidence found.",
        "ru": "Опровергающих доказательств не найдено.",
    },
    "evidence_kind_citation": {"en": "Source citation", "ru": "Цитата источника"},
    "evidence_kind_hypothesis_evidence": {
        "en": "Inference-engine evidence",
        "ru": "Доказательство из inference-движка",
    },
    "evidence_kind_dna_match": {"en": "DNA match", "ru": "ДНК-совпадение"},
    "evidence_kind_off_catalog_evidence": {
        "en": "Off-catalog evidence (Phase 22.5)",
        "ru": "Off-catalog доказательство (Phase 22.5)",
    },
    "evidence_kind_inference_rule": {"en": "Inference rule", "ru": "Логическое правило"},
    "weight": {"en": "Weight", "ru": "Вес"},
    "match_certainty": {"en": "Match certainty", "ru": "Уверенность привязки"},
    "confidence_calculation": {"en": "Confidence calculation", "ru": "Расчёт достоверности"},
    "confidence_value": {"en": "Composite confidence", "ru": "Композитная достоверность"},
    "confidence_method": {"en": "Method", "ru": "Метод"},
    "method_bayesian_22_5": {
        "en": "Phase 22.5 weighted aggregation (Σ weight × match_certainty)",
        "ru": "Взвешенная агрегация Phase 22.5 (Σ weight × match_certainty)",
    },
    "method_naive_count": {"en": "Naive supporting/total count", "ru": "Простой подсчёт"},
    "method_asserted_only": {"en": "Asserted only — not evaluated", "ru": "Только заявлено"},
    "sources": {"en": "Sources", "ru": "Источники"},
    "no_sources": {
        "en": "No source citations attached to this claim.",
        "ru": "К этому утверждению не привязаны цитаты источников.",
    },
    "footnotes": {"en": "Footnotes", "ru": "Сноски"},
    "provenance_heading": {"en": "Provenance", "ru": "Происхождение"},
    "provenance_channel": {"en": "Channel", "ru": "Канал получения"},
    "provenance_cost": {"en": "Cost", "ru": "Стоимость"},
    "provenance_jurisdiction": {"en": "Jurisdiction", "ru": "Юрисдикция"},
    "provenance_archive": {"en": "Archive", "ru": "Архив"},
    "provenance_reference": {"en": "Request reference", "ru": "Номер запроса"},
    "provenance_notes": {"en": "Notes", "ru": "Заметки"},
    "provenance_migrated": {
        "en": "Provenance backfilled by migration; channel may be unknown.",
        "ru": "Provenance проставлен миграцией; канал может быть unknown.",
    },
    "methodology": {"en": "Methodology", "ru": "Методология"},
    "default_methodology": {
        "en": (
            "This relationship report aggregates citations, inference-engine "
            "evidence, and (when enabled) DNA matches connecting the two "
            "subjects. Each evidence piece is shown with its 22.5 weight "
            "(document tier 1–3) and match-certainty; the composite "
            "confidence is the weighted sum minus contradicting evidence. "
            "Provenance metadata for off-catalog evidence is included for "
            "reproducibility. Extended-distance claims (cousin, grandparent, "
            "aunt/uncle) are marked as derived; full chained evidence is the "
            "scope of Phase 24.4+."
        ),
        "ru": (
            "Отчёт по связи агрегирует цитаты, evidence из inference-движка "
            "и (при включении) ДНК-совпадения между двумя субъектами. Каждое "
            "доказательство показано с весом 22.5 (tier документа 1–3) "
            "и match-certainty; композитный confidence — взвешенная "
            "сумма минус опровергающие доказательства. Provenance-метаданные "
            "off-catalog evidence приведены для воспроизводимости. "
            "Дальние связи (двоюродные, дед/внук, тётя/племянник) помечены "
            "как производные; полная цепочка — область Phase 24.4+."
        ),
    },
    "researcher": {"en": "Researcher", "ru": "Исследователь"},
    "generated_at": {"en": "Generated", "ru": "Дата формирования"},
    "report_id_label": {"en": "Report ID", "ru": "ID отчёта"},
    "extended_claim_caveat": {
        "en": (
            "This is an extended-distance claim ({claim_label}). Phase 24.3 "
            "v1 does not chain through intermediate relatives for evidence "
            "aggregation; verify the linking generations in their own "
            "per-pair reports before relying on this document."
        ),
        "ru": (
            "Это дальняя связь ({claim_label}). Phase 24.3 v1 не строит "
            "цепочку через промежуточных родственников для агрегации "
            "evidence; проверьте каждое промежуточное звено в своих "
            "per-pair отчётах перед опорой на этот документ."
        ),
    },
    "direct_claim_unresolved": {
        "en": (
            "The claimed direct relationship ({claim_label}) was NOT found "
            "in the tree's Family records. Either the connecting Family "
            "row is missing, or the claim is incorrect. Add the linking "
            "Family + FamilyChild records before treating this report as "
            "defensible."
        ),
        "ru": (
            "Заявленная прямая связь ({claim_label}) НЕ найдена в записях "
            "Family дерева. Либо отсутствует связывающая Family-запись, "
            "либо утверждение неверно. Добавьте связывающие Family + "
            "FamilyChild перед опорой на этот отчёт."
        ),
    },
    "evidence_count_label": {"en": "Evidence pieces", "ru": "Единиц доказательств"},
    "counter_count_label": {"en": "Contradicting pieces", "ru": "Опровергающих единиц"},
    "sex_male": {"en": "Male", "ru": "Мужской"},
    "sex_female": {"en": "Female", "ru": "Женский"},
    "sex_unknown": {"en": "Unknown", "ru": "Не указан"},
}


def t(key: str, locale: ReportLocale) -> str:
    """Lookup строки. KeyError если ключа нет — лучше fail-fast в render."""
    entry = _STRINGS[key]
    return entry[locale]


def claim_label(claim: ClaimedRelationship, locale: ReportLocale) -> str:
    """Human-readable название claim'а."""
    return _CLAIM_LABELS[claim][locale]


def report_title(title_style: str, locale: ReportLocale) -> str:
    """Заголовок отчёта в зависимости от title_style."""
    if title_style == "client_friendly":
        return t("report_title_client", locale)
    return t("report_title_formal", locale)


def evidence_kind_label(kind: str, locale: ReportLocale) -> str:
    return t(f"evidence_kind_{kind}", locale)


def confidence_method_label(method: str, locale: ReportLocale) -> str:
    return t(f"method_{method}", locale)


def sex_label(sex: str | None, locale: ReportLocale) -> str:
    if sex == "M":
        return t("sex_male", locale)
    if sex == "F":
        return t("sex_female", locale)
    return t("sex_unknown", locale)


__all__ = [
    "claim_label",
    "confidence_method_label",
    "evidence_kind_label",
    "report_title",
    "sex_label",
    "t",
]
