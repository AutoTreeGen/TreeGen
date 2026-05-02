"""Pure-функция ранжирования listing'ов под query (Phase 22.1).

Дизайн: чистая функция от ``(listing, query)`` → score [0, 1]. Никакого I/O,
никакой DB. Тесты гонят на in-memory dict'ах.

Score = взвешенная сумма:
    + 0.50 — точный record_type match (или query без record_type)
    + 0.25 — year coverage (доля query window, попадающая в [year_from, year_to])
    + 0.10 — country match (всегда == 1.0 если country фильтр применён,
                 потому что DB-фильтр уже отсёк non-matching)
    + 0.10 — access bonus: online_catalog > paid_request > in_person_only
                 > intermediary_required > closed
    + 0.05 — recency: чем свежее ``last_verified``, тем выше

Ranking — стабильная сортировка: при равных scores — лексикографически
по ``(country, name)`` для детерминизма.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

# Bonus per access_mode — predictable user-facing «easier first».
_ACCESS_BONUS: dict[str, float] = {
    "online_catalog": 0.10,
    "paid_request": 0.07,
    "in_person_only": 0.04,
    "intermediary_required": 0.02,
    "closed": 0.0,
}

# Halflife для last_verified bonus в днях. 365 дней → bonus ≈ 0.025.
_VERIFIED_HALFLIFE_DAYS = 365.0


def score_listing(
    listing: dict[str, Any],
    *,
    record_type: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    today: dt.date | None = None,
) -> float:
    """Вычислить score для listing под фильтр query.

    Args:
        listing: dict с полями архива (см. ``ArchiveListing.to_dict``).
        record_type: запрошенный record_type (None — все).
        year_from / year_to: query year window. None — без ограничения.
        today: для тестов; default = ``date.today()``.

    Returns:
        Score в [0, 1.0].
    """
    today = today or dt.datetime.now(tz=dt.UTC).date()

    # ── record_type match ────────────────────────────────────────────────
    listing_types: list[str] = list(listing.get("record_types") or [])
    if record_type is None:
        # Без фильтра — full credit (мы не можем лучше rank без сигнала).
        rt_score = 0.50
    elif record_type in listing_types:
        rt_score = 0.50
    else:
        rt_score = 0.0

    # ── year coverage ────────────────────────────────────────────────────
    year_score = (
        _year_overlap_fraction(
            listing.get("year_from"),
            listing.get("year_to"),
            query_from=year_from,
            query_to=year_to,
        )
        * 0.25
    )

    # ── country (always 1.0 если query filter применён) ──────────────────
    country_score = 0.10  # listing уже отфильтрован DB-side по country

    # ── access bonus ─────────────────────────────────────────────────────
    access = str(listing.get("access_mode") or "")
    access_score = _ACCESS_BONUS.get(access, 0.0)

    # ── recency bonus ────────────────────────────────────────────────────
    last_verified = listing.get("last_verified")
    recency_score = 0.0
    if isinstance(last_verified, dt.date):
        days_old = max(0, (today - last_verified).days)
        # экспоненциальный decay; 0 days → 0.05, halflife → 0.025
        recency_score = 0.05 * (0.5 ** (days_old / _VERIFIED_HALFLIFE_DAYS))

    return rt_score + year_score + country_score + access_score + recency_score


def _year_overlap_fraction(
    listing_from: int | None,
    listing_to: int | None,
    *,
    query_from: int | None,
    query_to: int | None,
) -> float:
    """Доля query window, покрытая listing range. [0, 1].

    Семантика «нет ограничения» для каждой стороны nullable:

    - Если у listing нет года ни сверху, ни снизу — считаем «universal»,
      возвращаем 1.0 (нет основания пенализировать).
    - Если у query нет года — мы не можем оценить overlap, возвращаем 1.0
      (все listing'и одинаково релевантны по году).
    - Если только одна сторона listing задана — используем «open-ended»
      интервал с разумной нижней / верхней границей (1500 / current year)
      для арифметики overlap.
    """
    if listing_from is None and listing_to is None:
        return 1.0
    if query_from is None and query_to is None:
        return 1.0

    lf = listing_from if listing_from is not None else 1500
    lt = listing_to if listing_to is not None else dt.datetime.now(tz=dt.UTC).date().year
    qf = query_from if query_from is not None else 1500
    qt = query_to if query_to is not None else dt.datetime.now(tz=dt.UTC).date().year

    overlap_lo = max(lf, qf)
    overlap_hi = min(lt, qt)
    if overlap_hi < overlap_lo:
        return 0.0

    overlap_span = overlap_hi - overlap_lo + 1
    query_span = max(1, qt - qf + 1)
    return min(1.0, overlap_span / query_span)


def compute_privacy_blocked(
    listing: dict[str, Any],
    *,
    year_from: int | None = None,  # noqa: ARG001 — API symmetry с score_listing.
    year_to: int | None = None,
    today: dt.date | None = None,
) -> bool:
    """True если запрошенный год попадает в privacy window listing'а.

    Privacy window — ``last_year - privacy_window_years``: записи моложе
    этого года недоступны без direct-relative proof. Мы не блокируем
    показать listing — только маркируем, что запрос пользователя в этом
    году скорее всего отказ-получит.
    """
    today = today or dt.datetime.now(tz=dt.UTC).date()
    window = listing.get("privacy_window_years")
    if window is None:
        return False
    cutoff_year = today.year - int(window)
    # Если хоть какая-то часть query window попадает после cutoff — flag.
    upper_query = year_to if year_to is not None else dt.datetime.now(tz=dt.UTC).date().year
    return upper_query > cutoff_year


__all__ = ["compute_privacy_blocked", "score_listing"]
