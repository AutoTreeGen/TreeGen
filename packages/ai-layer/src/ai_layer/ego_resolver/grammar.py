"""Грамматический парсер relative-references (en / ru / translit-ru).

Никакого LLM — детерминированная state-machine. На входе строка вида
«my wife», «brother of my mother», «брат матери жены», «moja zhena
Olga»; на выходе :class:`ParsedReference` с relationship-path'ом
``[RelStep, ...]`` от ego и опциональным name-tail'ом для финальной
фильтрации по имени.

Поддержка языков:

* **English** — possessive-chain «my X's Y's Z». Order: ego → X → Y → Z.
  Apostrophe variants: ``'s``, ``’s``, ``s'`` (terminal, plural-possessive).
* **Russian (Cyrillic)** — nominative-then-genitive «X матери жены».
  Order: target первый, далее genitive-цепочка от target к ego;
  reverse-аем для канонической ego→target ориентации.
* **Russian (Latin translit)** — «moja zhena», «brat materi zheny».
  Покрываем самые частые формы небольшой transliteration-таблицей
  (см. ``_TRANSLIT_RU``); полноценный BGN/PCGN не нужен — relationship-
  словарь маленький.
* **Direct names** — если ни одна kinship-stopword не сработала, весь
  текст уходит в ``name_tail`` и резолвер ищет по name-index'у.

Mixed mode: trailing-token, который не входит в kinship-vocab,
сепарируется как ``name_tail`` («my wife's mother Olga» → path=[wife,
mother], name_tail=Olga; «брат жены Дворa» → path=[wife, brother],
name_tail=Дворa).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai_layer.ego_resolver.types import RelKind, RelStep, SexHint


@dataclass(frozen=True, slots=True)
class ParsedReference:
    """Результат :func:`parse_reference`.

    Attributes:
        path: Relationship-path от ego к target. Пустой — если ввод
            является чистым именем («Dvora») без kinship-tokens.
        name_tail: Trailing-name для финального фильтра кандидатов.
            ``None`` — если path заканчивается kinship-словом без имени.
        raw: Исходная строка (lowercase'нутая); для debug-логов.
    """

    path: tuple[RelStep, ...]
    name_tail: str | None
    raw: str = ""


# Kinship-vocab по типам отношения. Каждое слово раскрывается в (kind, sex_hint).
# Sex-агностичные ноды (``parent`` / ``child`` / ``spouse`` / ``sibling``) тоже
# тут — для случаев, когда пол реально не указан в вводе.
_EN_NOMINATIVE: dict[str, tuple[RelKind, SexHint | None]] = {
    "wife": ("spouse", "F"),
    "husband": ("spouse", "M"),
    "spouse": ("spouse", None),
    "partner": ("spouse", None),
    "mother": ("parent", "F"),
    "mom": ("parent", "F"),
    "mum": ("parent", "F"),
    "father": ("parent", "M"),
    "dad": ("parent", "M"),
    "parent": ("parent", None),
    "son": ("child", "M"),
    "daughter": ("child", "F"),
    "child": ("child", None),
    "kid": ("child", None),
    "brother": ("sibling", "M"),
    "sister": ("sibling", "F"),
    "sibling": ("sibling", None),
}

# Russian nominative — первое слово фразы («брат жены», «мать»).
_RU_NOMINATIVE: dict[str, tuple[RelKind, SexHint | None]] = {
    "жена": ("spouse", "F"),
    "муж": ("spouse", "M"),
    "супруг": ("spouse", "M"),
    "супруга": ("spouse", "F"),
    "партнёр": ("spouse", None),
    "партнер": ("spouse", None),
    "мать": ("parent", "F"),
    "мама": ("parent", "F"),
    "отец": ("parent", "M"),
    "папа": ("parent", "M"),
    "родитель": ("parent", None),
    "сын": ("child", "M"),
    "дочь": ("child", "F"),
    "ребёнок": ("child", None),
    "ребенок": ("child", None),
    "брат": ("sibling", "M"),
    "сестра": ("sibling", "F"),
}

# Russian genitive — следует после nominative («брат жены», «жены» = genitive
# of wife). Walker строит path right-to-left от nominative target'а к ego.
# Слово «супруга» омонимично: genitive of «супруг» (M) и nominative of «супруга»
# (F). В genitive-словаре оставляем M-версию, потому что genitive-стадия
# идёт после уже определённого target'а — омонимия здесь не критична.
_RU_GENITIVE: dict[str, tuple[RelKind, SexHint | None]] = {
    "жены": ("spouse", "F"),
    "мужа": ("spouse", "M"),
    "супруга": ("spouse", "M"),
    "супруги": ("spouse", "F"),
    "партнёра": ("spouse", None),
    "партнера": ("spouse", None),
    "матери": ("parent", "F"),
    "мамы": ("parent", "F"),
    "отца": ("parent", "M"),
    "папы": ("parent", "M"),
    "родителя": ("parent", None),
    "сына": ("child", "M"),
    "дочери": ("child", "F"),
    "ребёнка": ("child", None),
    "ребенка": ("child", None),
    "брата": ("sibling", "M"),
    "сестры": ("sibling", "F"),
}

# Latin-romanized варианты русских kinship-слов. Покрывает самые частые
# user-input'ы (Telegram / голос). Не претендует на полный BGN/PCGN —
# скрипт relationship-словаря маленький, hand-curated проще.
_TRANSLIT_RU: dict[str, str] = {
    # nominative
    "zhena": "жена",
    "muzh": "муж",
    "suprug": "супруг",
    "supruga": "супруга",
    "mat": "мать",
    "mama": "мама",
    "otets": "отец",
    "papa": "папа",
    "syn": "сын",
    "doch": "дочь",
    "rebenok": "ребёнок",
    "rebyonok": "ребёнок",
    "brat": "брат",
    "sestra": "сестра",
    # genitive
    "zheny": "жены",
    "muzha": "мужа",
    "suprugi": "супруги",
    "materi": "матери",
    "mamy": "мамы",
    "ottsa": "отца",
    "papy": "папы",
    "syna": "сына",
    "docheri": "дочери",
    "rebyonka": "ребёнка",
    "rebenka": "ребёнка",
    "brata": "брата",
    "sestry": "сестры",
}

# Ego-markers: «my» / «мой» / «моя» / ... + transliterated formы. После strip'а
# не вносят в path — просто сигналят «вот тут ego». Множественные ego-markers
# («my wife's mother» → «my» один раз) не дублируем, токены матчатся индивидуально.
_EGO_MARKERS_EN = frozenset({"my", "mine"})
_EGO_MARKERS_RU = frozenset(
    {
        "мой",
        "моя",
        "моё",
        "мое",
        "мои",
        "моего",
        "моей",
        "моих",
        "моему",
        "моим",
        "моими",
    }
)
_EGO_MARKERS_TRANSLIT = frozenset(
    {
        "moy",
        "moi",
        "moja",
        "moya",
        "moje",
        "moe",
        "moego",
        "moey",
        "moyey",
        "moih",
    }
)

# English «of»-bridge: «brother of my mother» — синтаксический алиас «my
# mother's brother». Поддерживаем для голосовой транскрипции, где TTS чаще
# выдаёт «of»-форму.
_EN_OF_BRIDGE = frozenset({"of"})

# Possessive-suffix варианты: '«s», ’s, s’ — нормализуем все три к 's.
_POSSESSIVE_PATTERN = re.compile(r"['’]s\b|s['’](?=\s|$)")

_CYRILLIC_PATTERN = re.compile(r"[Ѐ-ӿ]")

_TOKEN_PATTERN = re.compile(r"[\w'’]+", re.UNICODE)


def _has_cyrillic(text: str) -> bool:
    """True если строка содержит хоть один Cyrillic-кодпоинт."""
    return bool(_CYRILLIC_PATTERN.search(text))


def _split_possessive(text: str) -> str:
    """«wife's» → «wife 's» — отделяем possessive marker как самостоятельный токен.

    Также нормализуем typographic apostrophe '’' → '\\''. Plural-possessive
    'parents'' → 'parents \\''  (terminal-only) обрабатываем отдельным
    fallback'ом ниже.
    """
    text = text.replace("’", "'")
    return re.sub(r"'s\b", " 's", text)


def _tokenize(text: str) -> list[str]:
    """Lowercase + split на whitespace + punctuation.

    Сохраняем possessive-marker «'s» как отдельный токен (после
    :func:`_split_possessive` он уже отделён пробелом). Все прочие
    punctuation-символы (запятые, точки) — выкидываем.
    """
    normalized = _split_possessive(text.lower())
    return _TOKEN_PATTERN.findall(normalized)


def _resolve_translit(token: str) -> str:
    """Latin-translit → Cyrillic (если в таблице); иначе вернуть как есть."""
    return _TRANSLIT_RU.get(token, token)


def _is_ego_marker(token: str) -> bool:
    return token in _EGO_MARKERS_EN or token in _EGO_MARKERS_RU or token in _EGO_MARKERS_TRANSLIT


@dataclass(slots=True)
class _ParseState:
    """Внутренний контейнер для аккумулирования результата парсера."""

    path_en: list[RelStep] = field(default_factory=list)  # ego → target ordering
    path_ru: list[RelStep] = field(default_factory=list)  # target → ego (reversed at end)
    name_tail_tokens: list[str] = field(default_factory=list)


def _parse_english(tokens: list[str]) -> ParsedReference | None:
    """Parses «my X's Y's Z [Name]» / «X of Y of Z [Name]».

    Returns ``None`` если ни один token не входит в ``_EN_NOMINATIVE`` —
    caller тогда попробует Russian / direct-name стратегии.
    """
    state = _ParseState()
    saw_kinship = False
    of_chain: list[RelStep] = []  # для «brother of my mother» — копим right-to-left
    in_of_mode = False

    for tok in tokens:
        if _is_ego_marker(tok):
            continue
        if tok in _EN_OF_BRIDGE:
            in_of_mode = True
            continue
        if tok == "'s":
            # Possessive marker сам по себе ничего не добавляет в path —
            # path уже наполнен предыдущим nominative-токеном.
            continue
        if tok in _EN_NOMINATIVE:
            kind, sex = _EN_NOMINATIVE[tok]
            step = RelStep(kind=kind, sex_hint=sex, word=tok)
            saw_kinship = True
            if in_of_mode:
                of_chain.append(step)
            else:
                state.path_en.append(step)
            continue
        # Не kinship и не connector → name-tail token. Допускаем многословные
        # имена («Dvora Levina») — собираем все trailing-non-kinship токены.
        state.name_tail_tokens.append(tok)

    if not saw_kinship and not state.name_tail_tokens:
        return None
    if not saw_kinship:
        return None

    # «brother of my mother» → target=brother, then «of my mother» добавляет
    # mother перед brother (т.е. ego → mother → brother). of_chain читается
    # left-to-right: первый of-step — ближайший к ego.
    if of_chain:
        # of_chain содержит [mother] (из «of my mother»). Добавляем в обратном
        # порядке перед уже накопленным path_en'ом: ego → mother → brother.
        state.path_en = list(reversed(of_chain)) + state.path_en

    name_tail = " ".join(state.name_tail_tokens) if state.name_tail_tokens else None
    return ParsedReference(
        path=tuple(state.path_en),
        name_tail=name_tail,
    )


def _parse_russian(tokens: list[str]) -> ParsedReference | None:
    """Парсит «(моя)? <nom> (<gen>)*  (Name)?» с reverse'ом в ego→target.

    Конвертирует translit-формы в Cyrillic перед look-up'ом
    (``zhena`` → ``жена``).
    """
    state = _ParseState()
    saw_kinship = False
    target_locked = False  # после первого nominative больше никаких nom-форм

    for tok in tokens:
        cyr = _resolve_translit(tok)
        if _is_ego_marker(cyr) or _is_ego_marker(tok):
            continue

        if not target_locked and cyr in _RU_NOMINATIVE:
            kind, sex = _RU_NOMINATIVE[cyr]
            state.path_ru.append(RelStep(kind=kind, sex_hint=sex, word=tok))
            saw_kinship = True
            target_locked = True
            continue

        if cyr in _RU_GENITIVE:
            kind, sex = _RU_GENITIVE[cyr]
            state.path_ru.append(RelStep(kind=kind, sex_hint=sex, word=tok))
            saw_kinship = True
            continue

        # Не kinship — это часть имени (или второй кандидат на target,
        # которого мы игнорируем чтобы не плодить ambiguity в грамматике).
        state.name_tail_tokens.append(tok)

    if not saw_kinship:
        return None

    # path_ru идёт target → ego; для канонической ориентации ego → target
    # переворачиваем. ``[brother, wife]`` → ``[wife, brother]``.
    ego_to_target = list(reversed(state.path_ru))
    name_tail = " ".join(state.name_tail_tokens) if state.name_tail_tokens else None
    return ParsedReference(
        path=tuple(ego_to_target),
        name_tail=name_tail,
    )


def parse_reference(text: str) -> ParsedReference:
    """Парсит relative-reference в :class:`ParsedReference`.

    Алгоритм:

    1. Tokenize (lowercase, split possessives, drop punctuation).
    2. Detect язык: Cyrillic в input'е → Russian-парсер; иначе сначала English,
       потом Russian-translit fallback (для «moja zhena»).
    3. Если ни один парсер не нашёл kinship-токены — весь ввод трактуется
       как имя (``path=(), name_tail=text.lower()``).

    Args:
        text: Произвольная строка от user'а. Любая casing'а / punctuation.

    Returns:
        :class:`ParsedReference`. Поле ``raw`` — нормализованный input;
        ``path`` — ego→target chain; ``name_tail`` — опциональный трейлинг
        для name-фильтра.
    """
    raw = text.strip().lower()
    tokens = _tokenize(text)
    if not tokens:
        return ParsedReference(path=(), name_tail=None, raw=raw)

    if _has_cyrillic(text):
        ru = _parse_russian(tokens)
        if ru is not None:
            return ParsedReference(path=ru.path, name_tail=ru.name_tail, raw=raw)
        # Чистое-имя в Cyrillic'е («Дворa Левина») — без relationship-токенов.
        return ParsedReference(path=(), name_tail=raw, raw=raw)

    en = _parse_english(tokens)
    if en is not None:
        return ParsedReference(path=en.path, name_tail=en.name_tail, raw=raw)

    # Translit-Russian fallback: «moja zhena» / «brat materi zheny».
    ru = _parse_russian(tokens)
    if ru is not None:
        return ParsedReference(path=ru.path, name_tail=ru.name_tail, raw=raw)

    # Чистое имя ASCII («Dvora» / «Olga»).
    return ParsedReference(path=(), name_tail=raw, raw=raw)


__all__ = ["ParsedReference", "parse_reference"]
