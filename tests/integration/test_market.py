"""snapshot() against a real SQLite DB in tmp_path."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from dirtbike_tracker.application.market import snapshot
from dirtbike_tracker.infrastructure.kijiji_scraper import Listing
from dirtbike_tracker.infrastructure.storage import (
    active_listings,
    connect,
    init_db,
    mark_stale,
    sold_listings,
    upsert_listings,
)


def _add(
    db: Path, ext_id: str, price: float | None, days_ago: int, source: str = "kijiji", **cols: int
) -> None:
    seen = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)).isoformat()
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO listings (source, external_id, url, title, price, first_seen, last_seen,"
            " is_active, is_primary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source,
                ext_id,
                f"https://x/{ext_id}",
                ext_id,
                price,
                seen,
                seen,
                cols.get("is_active", 1),
                cols.get("is_primary", 1),
            ),
        )
        conn.execute(
            "INSERT INTO listing_models (listing_id, model_slug) VALUES (?, 'crf150f')",
            (cur.lastrowid,),
        )


def test_snapshot_scores_active_primary_listings(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    _add(db, "cheap", 1400, days_ago=5)
    _add(db, "mid", 2000, days_ago=40)
    _add(db, "pricey", 2600, days_ago=60)
    _add(db, "crosspost", 1000, days_ago=5, is_primary=0)  # excluded from market + listings
    _add(db, "ancient", 9000, days_ago=200)  # outside 90d market window, still active
    _add(db, "junk", 50, days_ago=5)  # <= MIN_BIKE_PRICE: not market, not rated

    snap = snapshot("crf150f", db_path=db)

    assert snap.stats == {"low": 1400, "median": 2000, "high": 2600, "count": 3}
    assert snap.trend == -39.1  # recent 1400 vs older median 2300
    assert snap.listings[0]["external_id"] == "cheap"  # -30%, best deal first
    assert snap.listings[-1]["external_id"] == "junk"
    assert snap.listings[-1]["rating"] == "UNKNOWN"
    assert {s["external_id"] for s in snap.listings} == {
        "cheap",
        "mid",
        "pricey",
        "ancient",
        "junk",
    }


def test_same_price_listings_each_count(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    _add(db, "dealer-a", 4749, days_ago=5)
    _add(db, "dealer-b", 4749, days_ago=6)
    _add(db, "private", 3000, days_ago=7)

    snap = snapshot("crf150f", db_path=db)

    assert snap.stats == {"low": 3000, "median": 4749, "high": 4749, "count": 3}


def _row(db: Path, ext_id: str) -> tuple:
    with connect(db) as conn:
        r = conn.execute(
            "SELECT is_active, sold_at FROM listings WHERE external_id=?", (ext_id,)
        ).fetchone()
    return (r["is_active"], r["sold_at"])


def test_mark_stale_tags_likely_sold_only_for_sources_that_scraped(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    _add(db, "gone-4d", 2500, days_ago=4)
    _add(db, "gone-2d", 2600, days_ago=2)
    _add(db, "fresh", 2700, days_ago=0)
    _add(db, "at-gone-4d", 2800, days_ago=4, source="autotrader")  # source broken this run

    assert mark_stale(["kijiji"], db_path=db) == 2

    is_active, sold_at = _row(db, "gone-4d")
    assert is_active == 0
    assert sold_at is not None
    assert _row(db, "gone-2d") == (0, None)  # inactive, not yet "sold"
    assert _row(db, "fresh") == (1, None)
    assert _row(db, "at-gone-4d") == (1, None)
    assert mark_stale([], db_path=db) == 0


def test_sold_listings_feed_snapshot_and_relisting_clears_tag(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    _add(db, "sold", 2500, days_ago=5)
    mark_stale(["kijiji"], db_path=db)

    sold = sold_listings("crf150f", db_path=db)
    assert [(s["external_id"], s["days_listed"]) for s in sold] == [("sold", 0)]
    assert [s["external_id"] for s in snapshot("crf150f", db_path=db).sold] == ["sold"]

    relisted = Listing(
        source="kijiji",
        external_id="sold",
        url="https://x/sold",
        title="sold",
        price=2400,
        price_state="numeric",
        location="",
        posted_text="",
        description="",
        model_slugs=["crf150f"],
        scraped_at="",
    )
    upsert_listings([relisted], db_path=db)

    assert _row(db, "sold") == (1, None)
    assert sold_listings("crf150f", db_path=db) == []


def test_age_prefers_source_post_date_and_backfills_existing_rows(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    _add(db, "old-post", 2000, days_ago=0)  # first seen today
    _add(db, "no-post-date", 2100, days_ago=3)
    posted = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    seen_again = Listing(
        source="kijiji",
        external_id="old-post",
        url="https://x/old-post",
        title="old-post",
        price=2000,
        price_state="numeric",
        location="",
        posted_text=posted,
        description="",
        model_slugs=["crf150f"],
        scraped_at="",
    )

    upsert_listings([seen_again], db_path=db)

    ages = {r["external_id"]: r["age_days"] for r in active_listings("crf150f", db_path=db)}
    assert round(ages["old-post"]) == 20
    assert round(ages["no-post-date"]) == 3


def test_snapshot_weekly_starts_from_source_post_date(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    _add(db, "posted-long-ago", 2500, days_ago=0)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO price_history (listing_id, price, observed_at) SELECT id, price, first_seen FROM listings"
        )
        posted = (datetime.now(UTC) - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        conn.execute("UPDATE listings SET posted_text=?", (posted,))

    weekly = snapshot("crf150f", db_path=db).weekly

    assert len(weekly) == 13
    assert [m for _, m in weekly[-3:]] == [2500, 2500, 2500]
    assert weekly[0][1] is None
