"""Дедуп scoring для Place.

Реальные place-строки из GEDCOM:

* «Slonim»
* «Slonim, Grodno»
* «Slonim, Grodno Governorate, Russian Empire»
* «Slonim, Belarus»

Это всё одно место, описанное с разной глубиной иерархии. Алгоритм
(см. ADR-0015) — token_set_ratio + substring containment boost: если
короткая строка целиком является префиксом длинной (token-by-token),
поднимаем score на 0.15.
"""

from __future__ import annotations

from entity_resolution.string_matching import token_set_ratio

_TOKEN_SEP = ","


def _tokens(value: str) -> list[str]:
    """Раскладываем place-строку на token'ы по запятой."""
    return [t.strip().lower() for t in value.split(_TOKEN_SEP) if t.strip()]


def _is_prefix_subset(short: list[str], long: list[str]) -> bool:
    """``short`` — token-prefix ``long``? (Slonim ⊂ Slonim, Grodno, Russian Empire)."""
    if not short or len(short) > len(long):
        return False
    return long[: len(short)] == short


def place_match_score(a: str, b: str) -> float:
    """Композитный score сходства двух place-строк в [0, 1].

    Логика:

    1. Если одна строка — token-prefix другой («Slonim» ⊂ «Slonim,
       Grodno, Russian Empire»), это иерархический match: одно и то же
       место, просто описано с разной точностью. Возвращаем ≥ 0.85 (с
       плавным повышением до 1.0 по token_set_ratio). Это лечит главную
       боль — дубль с разной глубиной иерархии.
    2. Иначе — pure token_set_ratio, который уже умеет порядок токенов
       и пересечения.

    Returns:
        Score в [0, 1]. ≥ 0.80 → likely duplicate.
    """
    base = token_set_ratio(a, b)

    a_tokens = _tokens(a)
    b_tokens = _tokens(b)
    if a_tokens and b_tokens:
        if len(a_tokens) <= len(b_tokens):
            short, long = a_tokens, b_tokens
        else:
            short, long = b_tokens, a_tokens
        if _is_prefix_subset(short, long):
            # Иерархический match → высокая база 0.85 + bonus по token_set.
            return min(1.0, 0.85 + 0.15 * base)

    return min(base, 1.0)


__all__ = ["place_match_score"]
