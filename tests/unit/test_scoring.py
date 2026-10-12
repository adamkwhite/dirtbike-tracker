"""Pure domain scoring: no DB, no network, no mocks."""

from datetime import datetime, timedelta

import pytest

from dirtbike_tracker.domain.scoring import (
    market_stats,
    needs_detail,
    parse_km,
    parse_year,
    price_per_year,
    project_fit,
    project_stats,
    rate_listing,
    score_listings,
    specs_label,
    trend,
    urgency,
    weekly_medians,
)


@pytest.mark.parametrize(
    ("price", "rating", "delta_pct"),
    [
        (690, "DEAL", -31.0),
        (700, "DEAL", -30.0),  # -30% boundary is inclusive
        (710, "GOOD", -29.0),
        (900, "GOOD", -10.0),  # -10% boundary is inclusive
        (910, "FAIR", -9.0),
        (1150, "FAIR", 15.0),  # +15% boundary is inclusive
        (1160, "HIGH", 16.0),
    ],
)
def test_rate_listing_thresholds(price: float, rating: str, delta_pct: float) -> None:
    assert rate_listing(price, 1000) == {"rating": rating, "delta_pct": delta_pct}


@pytest.mark.parametrize("price", [1, 50, 100])  # MIN_BIKE_PRICE is inclusive
def test_rate_listing_never_rates_junk_prices(price: float) -> None:
    # a $100 set of plastics was rated DEAL (-97%) against a $3,400 TT-R125 median
    assert rate_listing(price, 3400) == {"rating": "UNKNOWN", "delta_pct": None}


def test_rate_listing_rates_just_above_floor() -> None:
    assert rate_listing(101, 3400)["rating"] == "DEAL"


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
        {"id": "high", "price": 1200},
        {"id": "unpriced", "price": None},
        {"id": "deal", "price": 600},
        {"id": "fair", "price": 1000},
    ]

    scored = score_listings(listings, median_price=1000)

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
        [{"price": 600, "age_days": 0.2}, {"price": 850, "age_days": 20}], median_price=1000
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


@pytest.mark.parametrize(
    ("text", "fit", "flags"),
    [
        ("2007 ttr-125", 5, []),
        ("CRF150F | Plated + Ownership | Runs Like New", 7, ["+ownership"]),  # runner: neutral
        (
            "Doesn\u2019t run, needs carb clean. Have ownership",
            10,
            ["+doesn't run", "+needs carb", "+ownership"],
        ),
        ("No ownership, project bike", 4, ["-no ownership", "+project"]),
        ("Seized engine, parts bike", 2, ["-seized"]),  # parts: neutral
        ("Not a project, ready to ride", 5, []),
        ("Ex race bike, trail riding with my son", 6, ["-race bike", "+my son", "+trail"]),
    ],
)
def test_project_fit(text: str, fit: int, flags: list[str]) -> None:
    assert project_fit(text) == (fit, flags)


def test_score_listings_reads_fit_from_title_and_description() -> None:
    (listing,) = score_listings(
        [{"price": 2000, "title": "CRF150F", "description": "no title, runs great"}], 2000
    )

    assert listing["fit"] == 2


@pytest.mark.parametrize(
    ("rating", "price", "target", "fetched_at", "expected"),
    [
        ("DEAL", 1200, None, None, True),
        ("GOOD", 1800, None, None, True),
        ("FAIR", 2400, 2500, None, True),  # under target
        ("FAIR", 2400, None, None, False),
        ("UNKNOWN", 50, 2500, None, False),  # junk price is never a target hit
        ("DEAL", 1200, None, "2026-10-12T10:00:00", False),  # already read
    ],
)
def test_needs_detail(
    rating: str, price: float, target: float | None, fetched_at: str | None, expected: bool
) -> None:
    listing = {"rating": rating, "price": price, "detail_fetched_at": fetched_at}

    assert needs_detail(listing, target) is expected


def _market(*prices: float, text: str = "doesn't run") -> list[dict]:
    return [{"price": p, "title": "CRF150F", "description": text} for p in prices]


def test_project_median_needs_enough_project_prices() -> None:
    runners = _market(3000, 3200, text="runs great, ownership")

    assert project_stats(runners + _market(800, 900, 1000, 1100, 1200)) == {
        "project_median": 1000,
        "project_count": 5,
    }
    assert project_stats(runners + _market(800, 900, 1000, 1100)) == {
        "project_median": None,
        "project_count": 4,
    }


def test_project_bike_rated_against_project_median() -> None:
    bikes = [
        {"price": 1000, "title": "CRF150F, won't start"},
        {"price": 2000, "title": "CRF150F, runs great, ownership"},
    ]

    runner, project = score_listings(bikes, median_price=3000, project_median=1000)  # by delta

    assert (project["rating"], project["basis"]) == ("FAIR", "project")
    assert (runner["rating"], runner["basis"]) == ("DEAL", None)


def test_project_bike_without_project_median_falls_back_and_is_marked() -> None:
    (listing,) = score_listings([{"price": 1000, "title": "CRF150F project"}], median_price=3000)

    assert (listing["rating"], listing["basis"]) == ("DEAL", "fallback")


@pytest.mark.parametrize("text", ["ownership, trail ready", "seized engine", "not a project"])
def test_bonus_and_red_flags_are_not_project_condition(text: str) -> None:
    (listing,) = score_listings([{"price": 1000, "title": text}], 3000, project_median=1000)

    assert listing["basis"] is None


def test_real_ad_wording_marks_a_project() -> None:
    # 2003 CRF150F on Kijiji, 2026-10-12
    (listing,) = score_listings(
        [
            {
                "price": 1500,
                "title": "2003 honda crf150f",
                "description": "needs rings and the timing chain done",
            }
        ],
        3000,
    )

    assert listing["basis"] == "fallback"
    assert listing["fit_flags"] == ["+needs rings", "+timing chain"]


@pytest.mark.parametrize(
    ("text", "fit", "flags"),
    [
        ("Rebel 500, won't start, needs carb", 0, ["-needs carb", "-won't start"]),
        (
            "Rebel 500, runs great, safetied, ownership",
            10,
            ["+ownership", "+runs great", "+safetied"],
        ),
        ("Rebel 500 sold as is, no safety", 0, ["-as is", "-no safety"]),
        ("KLX300 street legal, as-is special", 2, ["-as-is"]),
        ("Rebel 500 project, ready for the trail", 2, ["-project"]),
    ],
)
def test_ride_goal_wants_a_runner(text: str, fit: int, flags: list[str]) -> None:
    assert project_fit(text, "ride") == (fit, flags)


def test_ride_goal_has_no_project_segment() -> None:
    (listing,) = score_listings(
        [{"price": 1000, "title": "Rebel 500, doesn't run"}], 3000, project_median=1000, goal="ride"
    )

    assert (listing["rating"], listing["basis"], listing["fit"]) == ("DEAL", None, 2)


@pytest.mark.parametrize(
    ("raw", "km"),
    [
        (16672, 16672),
        ("16672", 16672),
        ("25,538 km", 25538),
        ("0", None),
        ("1", None),
        (None, None),
    ],
)
def test_parse_km_skips_placeholders(raw: object, km: int | None) -> None:
    assert parse_km(raw) == km


@pytest.mark.parametrize(
    ("raw", "year"), [("2018", 2018), (2027, 2027), ("2028", None), ("1899", None), ("abc", None)]
)
def test_parse_year_rejects_implausible(raw: object, year: int | None) -> None:
    assert parse_year(raw, this_year=2026) == year


def test_specs_and_price_per_year() -> None:
    bike = {"price": 2500, "year": 2015, "km": 16672}

    assert specs_label(bike) == "2015 · 16,672 km"
    assert specs_label({"year": 2015}) == "2015"
    assert specs_label({}) == ""
    assert price_per_year(bike, this_year=2026) == 227  # #16's example
    assert price_per_year({"price": 9000, "year": 2026}, this_year=2026) == 9000
    assert price_per_year({"price": 2500}, this_year=2026) is None
