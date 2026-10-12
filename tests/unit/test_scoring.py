"""Pure domain scoring: no DB, no network, no mocks."""

from datetime import datetime, timedelta

import pytest

from dirtbike_tracker.domain.scoring import (
    market_stats,
    rate_listing,
    score_listings,
    trend,
    urgency,
    weekly_medians,
)


@pytest.mark.parametrize(
    ("price", "rating", "delta_pct"),
    [
        (69, "DEAL", -31.0),
        (70, "DEAL", -30.0),  # -30% boundary is inclusive
        (71, "GOOD", -29.0),
        (90, "GOOD", -10.0),  # -10% boundary is inclusive
        (91, "FAIR", -9.0),
        (115, "FAIR", 15.0),  # +15% boundary is inclusive
        (116, "HIGH", 16.0),
    ],
)
def test_rate_listing_thresholds(price: float, rating: str, delta_pct: float) -> None:
    assert rate_listing(price, 100) == {"rating": rating, "delta_pct": delta_pct}


@pytest.mark.parametrize(("price", "median_price"), [(None, 100), (100, None), (100, 0)])
def test_rate_listing_unknown_without_price_or_market(
    price: float | None, median_price: float | None
) -> None:
    assert rate_listing(price, median_price) == {"rating": "UNKNOWN", "delta_pct": None}


def test_market_stats_empty() -> None:
    assert market_stats([]) == {"low": None, "median": None, "high": None, "count": 0}


def test_market_stats_single_price() -> None:
    assert market_stats([2500]) == {"low": 2500, "median": 2500, "high": 2500, "count": 1}


def test_market_stats_many_prices() -> None:
    assert market_stats([3000, 1000, 2000, 4000]) == {
        "low": 1000,
        "median": 2500,
        "high": 4000,
        "count": 4,
    }


def test_trend_recent_vs_older_window() -> None:
    # recent median 2200, older median 2000 -> +10%
    assert trend(recent=[2200], older=[1900, 2000, 2100]) == 10.0


def test_trend_keeps_older_prices_that_match_recent_ones() -> None:
    # same sticker price in both windows is still an older data point
    assert trend(recent=[2000], older=[2000, 2000, 1000]) == 0.0


@pytest.mark.parametrize(("recent", "older"), [([], [2000]), ([2000], [])])
def test_trend_none_without_both_windows(recent: list[float], older: list[float]) -> None:
    assert trend(recent, older) is None


def test_score_listings_sorts_best_deal_first_and_unpriced_last() -> None:
    listings: list[dict] = [
        {"id": "high", "price": 120},
        {"id": "unpriced", "price": None},
        {"id": "deal", "price": 60},
        {"id": "fair", "price": 100},
    ]

    scored = score_listings(listings, median_price=100)

    assert [(s["id"], s["rating"]) for s in scored] == [
        ("deal", "DEAL"),
        ("fair", "FAIR"),
        ("high", "HIGH"),
        ("unpriced", "UNKNOWN"),
    ]


def test_score_listings_without_market_rates_everything_unknown() -> None:
    scored = score_listings([{"price": 100}], median_price=None)

    assert scored[0]["rating"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("rating", "age_days", "expected"),
    [
        ("DEAL", 0.5, "URGENT"),
        ("DEAL", 2.0, None),  # 2 days is no longer fresh
        ("GOOD", 1.0, None),
        ("GOOD", 14.0, None),
        ("GOOD", 14.5, "NEGOTIABLE"),
        ("DEAL", 30.0, None),  # stale DEALs keep plain DEAL handling
        ("FAIR", 30.0, None),
        ("DEAL", None, None),
    ],
)
def test_urgency(rating: str, age_days: float | None, expected: str | None) -> None:
    assert urgency(rating, age_days) == expected


def test_score_listings_attaches_urgency_from_age() -> None:
    scored = score_listings(
        [{"price": 60, "age_days": 0.2}, {"price": 85, "age_days": 20}], median_price=100
    )

    assert [(s["rating"], s["urgency"]) for s in scored] == [
        ("DEAL", "URGENT"),
        ("GOOD", "NEGOTIABLE"),
    ]


NOW = datetime(2026, 10, 12, 12, 0)


def _timeline(
    start_days_ago: float, end_days_ago: float, *prices: tuple[float, float | None]
) -> dict:
    """prices: (days_ago observed, price), oldest first."""
    return {
        "start": NOW - timedelta(days=start_days_ago),
        "end": NOW - timedelta(days=end_days_ago),
        "history": [(NOW - timedelta(days=d), p) for d, p in prices],
    }


def test_weekly_medians_tracks_listings_on_market_each_week() -> None:
    timelines = [
        _timeline(20, 0, (20, 3000)),  # listed throughout
        _timeline(20, 0, (20, 2000), (3, 1500)),  # price drop this week
        _timeline(20, 10, (20, 4000)),  # gone 10 days ago
    ]

    weeks = weekly_medians(timelines, NOW, weeks=3)

    assert [w for w, _ in weeks] == ["2026-09-21", "2026-09-28", "2026-10-05"]
    assert [m for _, m in weeks] == [3000, 3000, 2250]


def test_weekly_medians_uses_first_seen_price_before_first_observation() -> None:
    # posted 20 days ago (Kijiji date) but we only started watching 1 day ago
    weeks = weekly_medians([_timeline(20, 0, (1, 2500))], NOW, weeks=3)

    assert [m for _, m in weeks] == [2500, 2500, 2500]


def test_weekly_medians_gaps_and_junk_prices() -> None:
    weeks = weekly_medians([_timeline(3, 0, (3, 50)), _timeline(3, 0, (3, None))], NOW, weeks=2)

    assert [m for _, m in weeks] == [None, None]
