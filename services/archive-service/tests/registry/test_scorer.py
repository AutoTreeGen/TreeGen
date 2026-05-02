"""Pure unit-tests для archive_service.registry.scorer (Phase 22.1).

Никакого I/O — все тесты гонят на dict'ах. Покрывают:

* record_type match → +0.50
* year coverage → +0.25 * fraction
* country prefilter → +0.10 (всегда, listing уже отфильтрован DB-side)
* access bonus → +0.10 (online_catalog), 0.07 (paid_request), …
* recency decay → +0.05 (today), → 0.025 (year ago)
* privacy_blocked → True если query year > today.year - privacy_window
"""

from __future__ import annotations

import datetime as dt

from archive_service.registry.scorer import compute_privacy_blocked, score_listing


def _listing(**overrides: object) -> dict[str, object]:
    """Базовый dict-listing для тестов; перебивается через kwargs."""
    base: dict[str, object] = {
        "country": "UA",
        "name": "Test archive",
        "record_types": ["civil_birth"],
        "year_from": 1850,
        "year_to": 1950,
        "access_mode": "paid_request",
        "last_verified": dt.date(2026, 5, 3),
        "privacy_window_years": None,
    }
    base.update(overrides)
    return base


# ── record_type match ─────────────────────────────────────────────────────


def test_exact_record_type_match_full_credit() -> None:
    """Listing с exact record_type получает +0.50 за match."""
    score = score_listing(
        _listing(record_types=["civil_birth"]),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    # 0.50 (rt) + 0.25 (full year overlap) + 0.10 (country) + 0.07 (paid) + ~0.05 (today)
    assert score >= 0.95


def test_record_type_miss_zero_credit() -> None:
    """Listing без запрошенного record_type теряет 0.50."""
    score = score_listing(
        _listing(record_types=["military"]),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    # 0 (rt) + 0.25 + 0.10 + 0.07 + ~0.05 ≈ 0.47
    assert score < 0.50


def test_no_record_type_filter_full_credit() -> None:
    """Если query не задаёт record_type — все listing'и получают full credit."""
    score = score_listing(
        _listing(record_types=["military"]),
        record_type=None,
        today=dt.date(2026, 5, 3),
    )
    # full rt + universal year + country + paid + today ≈ 0.97
    assert score >= 0.95


# ── year coverage ─────────────────────────────────────────────────────────


def test_full_year_overlap_full_credit() -> None:
    """Query [1900,1910] полностью внутри listing [1850,1950] → +0.25."""
    score_full = score_listing(
        _listing(),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    score_no_year = score_listing(
        _listing(),
        record_type="civil_birth",
        year_from=None,
        year_to=None,
        today=dt.date(2026, 5, 3),
    )
    # Оба варианта — full year credit.
    assert abs(score_full - score_no_year) < 0.01


def test_partial_year_overlap_partial_credit() -> None:
    """Query [1940,1960] перекрывает listing [1850,1950] на 11/21 ≈ 52%."""
    full = score_listing(
        _listing(),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    partial = score_listing(
        _listing(),
        record_type="civil_birth",
        year_from=1940,
        year_to=1960,
        today=dt.date(2026, 5, 3),
    )
    # partial должен быть строго ниже full.
    assert partial < full
    # Year-component delta ≈ 0.25 * (1 - 11/21) ≈ 0.119
    assert (full - partial) > 0.10


def test_zero_overlap_zero_year_credit() -> None:
    """Query [2000,2020] не пересекает listing [1850,1950] → year_score = 0."""
    score = score_listing(
        _listing(),
        record_type="civil_birth",
        year_from=2000,
        year_to=2020,
        today=dt.date(2026, 5, 3),
    )
    # 0.50 (rt) + 0 (year) + 0.10 + 0.07 + ~0.05 ≈ 0.72
    assert 0.65 < score < 0.75


def test_listing_universal_years_universal_credit() -> None:
    """Listing без указания years → year_score = full (universal)."""
    score = score_listing(
        _listing(year_from=None, year_to=None),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    # ≈ 0.95
    assert score >= 0.95


# ── access bonus ──────────────────────────────────────────────────────────


def test_online_catalog_outranks_paid_request() -> None:
    """Online catalog +0.10, paid_request +0.07 — online должен быть выше."""
    online = score_listing(
        _listing(access_mode="online_catalog"),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    paid = score_listing(
        _listing(access_mode="paid_request"),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    assert online > paid
    assert (online - paid) >= 0.02  # 0.10 - 0.07


def test_closed_zero_access_bonus() -> None:
    """Closed архив теряет access bonus совсем."""
    closed = score_listing(
        _listing(access_mode="closed"),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    paid = score_listing(
        _listing(access_mode="paid_request"),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    assert paid > closed


# ── recency decay ─────────────────────────────────────────────────────────


def test_recency_decay_today_full_bonus() -> None:
    """last_verified == today → +0.05."""
    today = dt.date(2026, 5, 3)
    fresh = score_listing(
        _listing(last_verified=today),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=today,
    )
    one_year_ago = score_listing(
        _listing(last_verified=today - dt.timedelta(days=365)),
        record_type="civil_birth",
        year_from=1900,
        year_to=1910,
        today=today,
    )
    # Halflife = 365 → year-old ≈ 0.025; fresh = 0.05.
    assert fresh > one_year_ago
    assert (fresh - one_year_ago) >= 0.02


# ── privacy_blocked ───────────────────────────────────────────────────────


def test_privacy_window_flags_recent_query() -> None:
    """Privacy window 75 + today=2026 → cutoff 1951; query year >= 1952 = blocked."""
    listing = _listing(privacy_window_years=75)
    # Query 1900 — далеко до cutoff, не blocked.
    assert not compute_privacy_blocked(
        listing,
        year_from=1900,
        year_to=1910,
        today=dt.date(2026, 5, 3),
    )
    # Query 2000 — после cutoff 1951, blocked.
    assert compute_privacy_blocked(
        listing,
        year_from=2000,
        year_to=2020,
        today=dt.date(2026, 5, 3),
    )


def test_no_privacy_window_never_blocked() -> None:
    """Без privacy_window — никогда не blocked."""
    listing = _listing(privacy_window_years=None)
    assert not compute_privacy_blocked(
        listing,
        year_from=2020,
        year_to=2025,
        today=dt.date(2026, 5, 3),
    )


def test_privacy_blocked_when_query_year_to_omitted_uses_today() -> None:
    """Если query year_to не задан, используем today.year — блокирующий fallback."""
    listing = _listing(privacy_window_years=75)
    # Без year_to — implicit upper = today.year = 2026 > cutoff 1951.
    assert compute_privacy_blocked(
        listing,
        year_from=1900,
        year_to=None,
        today=dt.date(2026, 5, 3),
    )
