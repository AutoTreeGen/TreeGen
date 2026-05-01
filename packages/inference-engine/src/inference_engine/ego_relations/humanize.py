"""Многоязычный humanize для ``RelationshipPath``.

Поддерживаемые языки: ``en``, ``ru``, ``he``, ``nl``, ``de``. Hardcoded
строки вместо i18n-каталога — словарный объём небольшой, и тексты
вычисляются только в API/Chat UI surface'е, не в основной БД-логике
(см. ADR-0068 §Decision/i18n-strategy).

Принцип рендеринга:

- ``en``: «X's Y's Z» — chain с possessive's справа налево от ego.
  Path ``['wife', 'brother']`` → ``"wife's brother"``.
- ``ru``: «Z родительный_падеж(Y) родительный_падеж(X)» — родственное
  слово первым (target), затем дательная цепочка genitive'ов.
  Path ``['wife', 'brother']`` → ``"брат жены"``.
- ``de`` / ``nl`` / ``he``: «Z von Y von X» / «Z van Y van X» / «Z של Y של X»
  с language-specific предлогом.

Twin-флаг: если ``path.is_twin`` и последний segment — sibling
(``brother``/``sister``/``sibling``), prepend «twin» (или эквивалент)
в финальный термин на каждом языке.
"""

from __future__ import annotations

from typing import Literal

from inference_engine.ego_relations.types import RelationshipPath

Language = Literal["en", "ru", "he", "nl", "de"]
SUPPORTED_LANGUAGES: tuple[Language, ...] = ("en", "ru", "he", "nl", "de")


_NOMINATIVE: dict[Language, dict[str, str]] = {
    "en": {
        "self": "yourself",
        "wife": "wife",
        "husband": "husband",
        "spouse": "spouse",
        "mother": "mother",
        "father": "father",
        "parent": "parent",
        "son": "son",
        "daughter": "daughter",
        "child": "child",
        "brother": "brother",
        "sister": "sister",
        "sibling": "sibling",
    },
    "ru": {
        "self": "вы сами",
        "wife": "жена",
        "husband": "муж",
        "spouse": "супруг(а)",
        "mother": "мать",
        "father": "отец",
        "parent": "родитель",
        "son": "сын",
        "daughter": "дочь",
        "child": "ребёнок",
        "brother": "брат",
        "sister": "сестра",
        "sibling": "брат/сестра",
    },
    "he": {
        "self": "אתה עצמך",
        "wife": "אישה",
        "husband": "בעל",
        "spouse": "בן/בת זוג",
        "mother": "אם",
        "father": "אב",
        "parent": "הורה",
        "son": "בן",
        "daughter": "בת",
        "child": "ילד",
        "brother": "אח",
        "sister": "אחות",
        "sibling": "אח/אחות",
    },
    "nl": {
        "self": "jijzelf",
        "wife": "vrouw",
        "husband": "man",
        "spouse": "echtgeno(o)t(e)",
        "mother": "moeder",
        "father": "vader",
        "parent": "ouder",
        "son": "zoon",
        "daughter": "dochter",
        "child": "kind",
        "brother": "broer",
        "sister": "zus",
        "sibling": "broer/zus",
    },
    "de": {
        "self": "du selbst",
        "wife": "Ehefrau",
        "husband": "Ehemann",
        "spouse": "Ehepartner(in)",
        "mother": "Mutter",
        "father": "Vater",
        "parent": "Elternteil",
        "son": "Sohn",
        "daughter": "Tochter",
        "child": "Kind",
        "brother": "Bruder",
        "sister": "Schwester",
        "sibling": "Geschwister",
    },
}

_GENITIVE: dict[Language, dict[str, str]] = {
    "ru": {
        "wife": "жены",
        "husband": "мужа",
        "spouse": "супруга",
        "mother": "матери",
        "father": "отца",
        "parent": "родителя",
        "son": "сына",
        "daughter": "дочери",
        "child": "ребёнка",
        "brother": "брата",
        "sister": "сестры",
        "sibling": "брата/сестры",
    },
}

_TWIN_PREFIX: dict[Language, str] = {
    "en": "twin ",
    "ru": "-близнец",  # суффиксная композиция: «брат-близнец»
    "he": " תאום",  # «אח תאום»
    "nl": "tweeling",  # «tweelingbroer»
    "de": "Zwillings",  # «Zwillingsbruder»
}


def _is_sibling_term(part: str) -> bool:
    return part in {"brother", "sister", "sibling"}


def _apply_twin(language: Language, term: str, base_part: str) -> str:
    """Прикрепляет twin-маркер к слову в нужном языку формате."""
    marker = _TWIN_PREFIX[language]
    if language == "en":
        return marker + term  # "twin brother"
    if language == "ru":
        return term + marker  # "брат-близнец" / "сестра-близнец"
    if language == "he":
        # Hebrew sibling-word agrees in gender; «תאום» (m) / «תאומה» (f)
        suffix = " תאומה" if base_part == "sister" else " תאום"
        return term + suffix  # «אח תאום» / «אחות תאומה»
    if language == "nl":
        return marker + term  # "tweelingbroer" / "tweelingzus"
    if language == "de":
        return marker + term.lower()  # "Zwillingsbruder" / "Zwillingsschwester"
    return term


def _humanize_en(parts: list[str], is_twin: bool) -> str:
    """English: «wife's brother», «wife's mother's brother»."""
    nom = _NOMINATIVE["en"]
    rendered = [nom.get(p, p) for p in parts]
    if is_twin and _is_sibling_term(parts[-1]):
        rendered[-1] = _apply_twin("en", rendered[-1], parts[-1])
    if len(rendered) == 1:
        return rendered[0]
    return "'s ".join(rendered[:-1]) + "'s " + rendered[-1]


def _humanize_ru(parts: list[str], is_twin: bool) -> str:
    """Русский: target в номинативе, далее цепочка генитивов от ego.

    Path ``['wife', 'brother']`` → ``"брат жены"``.
    Path ``['wife', 'mother', 'brother']`` → ``"брат матери жены"``.
    """
    nom = _NOMINATIVE["ru"]
    gen = _GENITIVE["ru"]
    target_part = parts[-1]
    target_word = nom.get(target_part, target_part)
    if is_twin and _is_sibling_term(target_part):
        target_word = _apply_twin("ru", target_word, target_part)
    if len(parts) == 1:
        return target_word
    # genitives — справа налево вглубь от ego: ['wife', 'mother', 'brother']
    # → «брат матери жены»: brother + genitive(mother) + genitive(wife)
    chain = [gen.get(p, p) for p in reversed(parts[:-1])]
    return target_word + " " + " ".join(chain)


def _humanize_with_preposition(
    language: Language,
    parts: list[str],
    is_twin: bool,
    preposition: str,
) -> str:
    """Универсальный шаблон «Z {предлог} Y {предлог} X» (de/nl/he)."""
    nom = _NOMINATIVE[language]
    target_part = parts[-1]
    target_word = nom.get(target_part, target_part)
    if is_twin and _is_sibling_term(target_part):
        target_word = _apply_twin(language, target_word, target_part)
    if len(parts) == 1:
        return target_word
    chain = [nom.get(p, p) for p in reversed(parts[:-1])]
    return (
        target_word
        + " "
        + (" " + preposition + " ").join([preposition + " " + chain[0], *chain[1:]])
    )


def humanize(path: RelationshipPath, language: Language = "en") -> str:
    """Превращает ``RelationshipPath`` в человекочитаемую фразу.

    Twin disambiguation: если ``path.is_twin`` и target — sibling, вставляет
    «twin»-эквивалент (см. ADR-0068 §Decision/twin disambiguation).
    """
    if language not in _NOMINATIVE:
        msg = f"unsupported language {language!r}; supported: {SUPPORTED_LANGUAGES}"
        raise ValueError(msg)
    if path.kind == "self":
        return _NOMINATIVE[language]["self"]

    parts = path.kind.split(".")

    if language == "en":
        return _humanize_en(parts, path.is_twin)
    if language == "ru":
        return _humanize_ru(parts, path.is_twin)
    if language == "de":
        return _humanize_with_preposition("de", parts, path.is_twin, preposition="von")
    if language == "nl":
        return _humanize_with_preposition("nl", parts, path.is_twin, preposition="van")
    if language == "he":
        return _humanize_with_preposition("he", parts, path.is_twin, preposition="של")
    msg = f"unreachable language: {language}"
    raise AssertionError(msg)
