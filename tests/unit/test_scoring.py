"""Pure domain scoring: no DB, no network, no mocks."""

import pytest

from dirtbike_tracker.domain.scoring import (
    market_stats,
    rate_listing,
    score_listings,
    trend,
    urgency,
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
