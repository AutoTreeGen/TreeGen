"""NameMatcher (Phase 15.10 / ADR-0068).

Ranks candidates через variants + DM phonetic + fuzzy fallback, возвращая
:class:`MatchResult` с reason-attribution для каждого hit'а.

Алгоритм match'а на каждый кандидат:

1. **Exact** (case-insensitive после ASCII-fold) → 1.0.
2. **Diacritic-fold** equality → 0.92.
3. **Synonym-anchor** intersection → 0.88.
4. **Cross-script transliteration** intersection → 0.85.
5. **DM phonetic** intersection (только если ``use_phonetic`` включён) →
   0.75.
6. **Fuzzy** (Levenshtein) → ratio в [0.60, 0.80] (clamped).

Backward-compat: ``use_variants=False, use_phonetic=False,
use_synonyms=False`` → fallback только на fuzzy + exact (зеркалит
поведение чистого ``levenshtein_ratio``-сравнения, без variant-генерации).

Reason-priority идёт по порядку выше — первый совпавший reason берётся;
score кладём из соответствующего диапазона. ``via`` пишет debug-info
(какая variant-категория сработала, какой DM-код пересёкся).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from entity_resolution.names.daitch_mokotoff import dm_soundex
from entity_resolution.names.synonyms import canonical_form, load_icp_synonyms
from entity_resolution.names.transliterate import Transliterator
from entity_resolution.string_matching import levenshtein_ratio

MatchReason = Literal[
    "exact",
    "variant_transliteration",
    "variant_diacritic",
    "variant_synonym",
    "dm_phonetic",
    "fuzzy",
]

# Score-диапазоны соответствуют ADR-0068 §«Reason таблица».
_SCORE_EXACT: Final[float] = 1.0
_SCORE_DIACRITIC: Final[float] = 0.92
_SCORE_SYNONYM: Final[float] = 0.88
_SCORE_TRANSLITERATION: Final[float] = 0.85
_SCORE_DM_PHONETIC: Final[float] = 0.75
_FUZZY_MIN: Final[float] = 0.60
_FUZZY_MAX: Final[float] = 0.80


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Один candidate-match со score'ом + reason'ом.

    Attributes:
        candidate: Исходная candidate-строка (не canonicalize'д для UX'а).
        score: Composite score в [0.0, 1.0]. Higher is better.
        reason: Как был получен match — для UI explainability и audit-логов.
        via: Optional debug-payload (например, ``{"standard": "BGN"}`` или
            ``{"dm_code": "596300"}``). ``None`` — нет дополнительной информации.
    """

    candidate: str
    score: float
    reason: MatchReason
    via: dict[str, Any] | None = None


def _diacritic_variants(text: str, tl: Transliterator) -> set[str]:
    """Объединение Polish + German + Czech diacritic candidates.

    Используем lower-case'нутые формы для intersection check'а, но
    исходная casing сохраняется в set'е (caller сравнивает по
    canonical_form для consistency).
    """
    out: set[str] = set()
    for lang in ("pl", "de", "cs"):
        out.update(tl.normalize_diacritics(text, lang=lang))  # type: ignore[arg-type]
    return out


def _transliteration_variants(text: str, tl: Transliterator) -> set[str]:
    """All cross-script Latin / Cyrillic / Hebrew formы для произвольного input'а."""
    out: set[str] = set()
    for std in ("bgn", "iso9", "loc"):
        out.add(tl.to_latin(text, source_script="cyrillic", standard=std))  # type: ignore[arg-type]
    for std in ("bgn", "loc"):
        out.add(tl.to_latin(text, source_script="hebrew", standard=std))  # type: ignore[arg-type]
    out.add(tl.to_cyrillic(text, source_script="latin"))
    out.add(tl.to_hebrew(text, source_script="latin"))
    out.discard("")
    return out


def _synonym_set(text: str) -> frozenset[str]:
    """ICP anchor-set для конкретного name'а; пустой если не в таблице."""
    return load_icp_synonyms().get(canonical_form(text), frozenset())


def _strict_intersect(a: set[str] | frozenset[str], b: set[str] | frozenset[str]) -> bool:
    """Two sets intersect under same-script case-fold (lower + strip).

    НЕ unicode-folds (no unidecode) — поэтому ``Levitin`` и ``Левитин``
    НЕ матчатся здесь. Используется для diacritic-стадии: ``Müller``
    ≡ ``Mueller`` через German-fold rule, обе в Latin-script.
    """
    a_c = {x.strip().lower() for x in a if x}
    b_c = {x.strip().lower() for x in b if x}
    return bool(a_c & b_c)


def _canonical_intersect(a: set[str] | frozenset[str], b: set[str] | frozenset[str]) -> bool:
    """Two sets intersect under cross-script ASCII-fold (canonical_form).

    Folds Cyrillic / Hebrew → Latin через unidecode. ``Левитин`` ≡
    ``Levitin`` здесь матчатся. Используется для transliteration-стадии.
    """
    a_c = {canonical_form(x) for x in a if x}
    b_c = {canonical_form(x) for x in b if x}
    return bool(a_c & b_c)


def _dm_intersection(query: str, candidate: str) -> str | None:
    """Common DM phonetic code, if any. Returns one common code или None."""
    a = set(dm_soundex(query))
    b = set(dm_soundex(candidate))
    common = a & b
    if not common:
        return None
    # Deterministic pick: lexicographically smallest для воспроизводимости тестов.
    return min(common)


class NameMatcher:
    """Multi-strategy name matcher с reason-attribution'ом.

    Args:
        language: Опциональный ISO-код языка-контекста (``ru``, ``en``, ...).
            V1 hint игнорируется — собран для будущего routing'а на
            language-specific patronymic-парсер; кладём аргумент сейчас
            чтобы не ломать call-site'ы при добавлении.
        use_variants: Использовать variant-comparison'ы (transliteration +
            diacritic). По умолчанию ``True``.
        use_phonetic: Использовать DM phonetic intersection. По умолчанию
            ``True``.
        use_synonyms: Использовать ICP anchor-table. По умолчанию ``True``.

    Backward-compat: все три флага в ``False`` → :meth:`match` сводится
    к exact + fuzzy паттерну, идентичному прямому ``levenshtein_ratio``-
    использованию. Используется тестами, проверяющими что callers без
    миграции не ломаются.
    """

    def __init__(
        self,
        language: str | None = None,
        *,
        use_variants: bool = True,
        use_phonetic: bool = True,
        use_synonyms: bool = True,
    ) -> None:
        self._language = language
        self._use_variants = use_variants
        self._use_phonetic = use_phonetic
        self._use_synonyms = use_synonyms
        self._tl = Transliterator()

    @property
    def use_variants(self) -> bool:
        return self._use_variants

    @property
    def use_phonetic(self) -> bool:
        return self._use_phonetic

    @property
    def use_synonyms(self) -> bool:
        return self._use_synonyms

    def match(
        self,
        query: str,
        candidates: list[str],
        *,
        min_score: float = 0.7,
    ) -> list[MatchResult]:
        """Rank candidates against query, return matches above ``min_score``.

        Args:
            query: Имя для матчинга. Любой script.
            candidates: Список кандидатов. Может содержать дубликаты — мы
                их **не** дедупим (caller'ы могут хранить duplicates с
                разной мета-информацией).
            min_score: Min cutoff. Дефолт 0.7 — фильтрует чистый-fuzzy
                noise (Levenshtein < 0.7 обычно не одно имя).

        Returns:
            ``list[MatchResult]`` отсортированный по score DESC. Пустой
            список — никто не прошёл ``min_score``.
        """
        results: list[MatchResult] = []
        for candidate in candidates:
            scored = self._score_one(query, candidate)
            if scored is not None and scored.score >= min_score:
                results.append(scored)
        results.sort(key=lambda r: (-r.score, r.candidate))
        return results

    # ------------------------------------------------------------ helpers

    def _score_one(self, query: str, candidate: str) -> MatchResult | None:
        """Compute best-reason match для одной (query, candidate) пары.

        Возвращает первый reason, прошедший check, в порядке priority'а.
        Если ничего — fuzzy-fallback.
        """
        # 1. Exact: same script, case-insensitive (без unidecode-fold'а).
        # Cross-script equivalence (``Levitin`` == ``Левитин``) — это
        # ``variant_transliteration``, а НЕ exact.
        if query and query.strip().lower() == candidate.strip().lower():
            return MatchResult(candidate=candidate, score=_SCORE_EXACT, reason="exact")

        # Backward-compat: все три флага False → только fuzzy.
        if not (self._use_variants or self._use_phonetic or self._use_synonyms):
            return self._fuzzy_match(query, candidate)

        # 2. Diacritic-fold equality (если variants включены). Используем
        # _strict_intersect (без unidecode): cross-script equivalences
        # должны попасть в transliteration-стадию ниже, не сюда.
        if self._use_variants:
            q_diac = _diacritic_variants(query, self._tl)
            c_diac = _diacritic_variants(candidate, self._tl)
            if _strict_intersect(q_diac, c_diac):
                return MatchResult(
                    candidate=candidate,
                    score=_SCORE_DIACRITIC,
                    reason="variant_diacritic",
                )

        # 3. Synonym-anchor table.
        if self._use_synonyms:
            q_syn = _synonym_set(query)
            if q_syn and (canonical_form(candidate) in {canonical_form(v) for v in q_syn}):
                return MatchResult(
                    candidate=candidate,
                    score=_SCORE_SYNONYM,
                    reason="variant_synonym",
                )

        # 4. Cross-script transliteration.
        if self._use_variants:
            q_tlit = _transliteration_variants(query, self._tl)
            c_tlit = _transliteration_variants(candidate, self._tl)
            # Сравниваем query+tlit-set'ы с candidate+tlit-set'ами:
            # если query-translit совпадает с candidate (или vice-versa), —
            # это transliteration-match.
            full_q = q_tlit | {query}
            full_c = c_tlit | {candidate}
            if _canonical_intersect(full_q, full_c):
                return MatchResult(
                    candidate=candidate,
                    score=_SCORE_TRANSLITERATION,
                    reason="variant_transliteration",
                )

        # 5. DM phonetic.
        if self._use_phonetic:
            common = _dm_intersection(query, candidate)
            if common is not None:
                return MatchResult(
                    candidate=candidate,
                    score=_SCORE_DM_PHONETIC,
                    reason="dm_phonetic",
                    via={"dm_code": common},
                )

        # 6. Fuzzy fallback.
        return self._fuzzy_match(query, candidate)

    def _fuzzy_match(self, query: str, candidate: str) -> MatchResult | None:
        """Levenshtein ratio в [_FUZZY_MIN, _FUZZY_MAX] clamp'е."""
        ratio = levenshtein_ratio(query, candidate)
        if ratio <= 0.0:
            return None
        # Clamp ratio в [0.60, 0.80] так чтобы fuzzy никогда не «бил»
        # variant_*-reason'ы (priority через score-tier'ы). Без clamp'а
        # fuzzy 0.95 на «Levitin»/«Levitan» победил бы DM-match хорошо
        # описанной AJ-фамилии — мы хотим обратное (DM canonical, fuzzy
        # это последний resort).
        score = max(_FUZZY_MIN, min(_FUZZY_MAX, ratio))
        return MatchResult(candidate=candidate, score=score, reason="fuzzy")


__all__ = [
    "MatchReason",
    "MatchResult",
    "NameMatcher",
]
