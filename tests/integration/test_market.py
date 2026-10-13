"""snapshot() against a real SQLite DB in tmp_path."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dirtbike_tracker.application.market import snapshot
from dirtbike_tracker.domain.listing import Listing
from dirtbike_tracker.infrastructure.storage import (
    SqliteStore,
    active_listings,
    connect,
    init_db,
    mark_stale,
    set_contact,
    set_dismissed,
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

    snap = snapshot("crf150f", SqliteStore(db))

    assert snap.stats == {
        "low": 1400,
        "median": 2000,
        "high": 2600,
        "count": 3,
        "project_median": None,
        "project_count": 0,
        "sellers": "all",
    }
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

    snap = snapshot("crf150f", SqliteStore(db))

    assert snap.stats == {
        "low": 3000,
        "median": 4749,
        "high": 4749,
        "count": 3,
        "project_median": None,
        "project_count": 0,
        "sellers": "all",
    }


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
    assert [s["external_id"] for s in snapshot("crf150f", SqliteStore(db)).sold] == ["sold"]

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

    weekly = snapshot("crf150f", SqliteStore(db)).weekly

    assert len(weekly) == 13
    assert [m for _, m in weekly[-3:]] == [2500, 2500, 2500]
    assert weekly[0][1] is None


def _price_change(db: Path, ext_id: str, price: float) -> None:
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO price_history (listing_id, price, observed_at)"
            " SELECT id, ?, datetime('now', '+1 second') FROM listings WHERE external_id=?",
            (price, ext_id),
        )
        conn.execute("UPDATE listings SET price=? WHERE external_id=?", (price, ext_id))


def test_price_drops_from_history_feed_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    for ext_id, price in (("dropped", 3000), ("raised", 2500), ("steady", 2800)):
        _add(db, ext_id, price, days_ago=3)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO price_history (listing_id, price, observed_at)"
            " SELECT id, price, first_seen FROM listings"
        )
    _price_change(db, "dropped", 2500)
    _price_change(db, "raised", 2700)

    by_id = {s["external_id"]: s for s in snapshot("crf150f", SqliteStore(db)).listings}

    assert by_id["dropped"]["price_drop_from"] == 3000
    assert by_id["raised"]["price_drop_from"] is None
    assert by_id["steady"]["price_drop_from"] is None


def _listing(source: str, ext_id: str, url: str, price: float) -> Listing:
    return Listing(
        source=source,
        external_id=ext_id,
        url=url,
        title=ext_id,
        price=price,
        price_state="numeric",
        location="",
        posted_text="",
        description="",
        model_slugs=["crf150f"],
        scraped_at="",
    )


@pytest.mark.parametrize(
    "ref",
    [
        "1743797617",
        "https://www.kijiji.ca/v-sport-other/oakville/yamaha-plastics/1743797617?utm=x",
    ],
)
def test_dismiss_hides_listing_but_keeps_market_and_survives_rescrape(
    tmp_path: Path, ref: str
) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    kij = _listing(
        "kijiji",
        "1743797617",
        "https://www.kijiji.ca/v-sport-other/oakville/yamaha-plastics/1743797617",
        2000,
    )
    other = _listing("kijiji", "1700000001", "https://x/1700000001", 3000)
    upsert_listings([kij, other], db_path=db)

    assert set_dismissed(ref, True, db_path=db) == ["1743797617"]
    upsert_listings([kij, other], db_path=db)  # seen again by the next scrape

    snap = snapshot("crf150f", SqliteStore(db))
    assert [s["external_id"] for s in snap.listings] == ["1700000001"]
    assert snap.stats["count"] == 2  # still market data

    assert set_dismissed(ref, False, db_path=db) == ["1743797617"]
    assert len(snapshot("crf150f", SqliteStore(db)).listings) == 2


def test_dismiss_by_autotrader_url_and_unknown_ref(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    uuid = "a5332962-fd58-4cd1-b003-3ca4229eba2d"
    at_url = f"https://www.autotrader.ca/motorcycles-atvs/offers/kawasaki-klx-140-oem-{uuid}"
    upsert_listings([_listing("autotrader", uuid, at_url, 2000)], db_path=db)

    assert set_dismissed(at_url, True, db_path=db) == [uuid]
    assert set_dismissed("https://www.kijiji.ca/v-x/y/999", True, db_path=db) == []


def test_contact_by_url_keeps_note_survives_rescrape_and_clears(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    bike = _listing(
        "kijiji", "1742579252", "https://www.kijiji.ca/v-x/muskoka/crf/1742579252", 1200
    )
    upsert_listings([bike], db_path=db)
    url = "https://www.kijiji.ca/v-x/muskoka/crf/1742579252?utm=x"
    ten_days_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10, hours=1)

    assert set_contact(url, ten_days_ago, "offered 750", db_path=db) == ["1742579252"]
    upsert_listings([bike], db_path=db)  # rescrape doesn't touch it
    assert set_contact("1742579252", ten_days_ago, None, db_path=db) == ["1742579252"]

    (listing,) = snapshot("crf150f", SqliteStore(db)).listings
    assert int(listing["contacted_days"]) == 10
    assert listing["contact_note"] == "offered 750"  # no new note keeps the old one

    set_contact("1742579252", None, db_path=db)
    (listing,) = snapshot("crf150f", SqliteStore(db)).listings
    assert (listing["contacted_days"], listing["contact_note"]) == (None, None)
    assert set_contact("999", None, db_path=db) == []


def test_ride_snapshot_skips_the_project_median(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    upsert_listings([_listing("kijiji", "1700000001", "https://x/1700000001", 4000)], db_path=db)

    assert "project_median" not in snapshot("crf150f", SqliteStore(db), goal="ride").stats


def test_year_km_and_photo_stored_and_kept_when_a_rescrape_lacks_them(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    bike = _listing("kijiji", "1742893636", "https://x/1742893636", 5250)
    bike.year, bike.km, bike.image_url = 2018, 16672, "https://media.kijiji.ca/a.jpg"
    bike.seller_type = "dealer"
    upsert_listings([bike], db_path=db)
    bike.year, bike.km, bike.image_url = None, None, None  # fields didn't load
    bike.seller_type = None
    upsert_listings([bike], db_path=db)

    (listing,) = snapshot("crf150f", SqliteStore(db)).listings
    assert (listing["year"], listing["km"]) == (2018, 16672)
    assert listing["image_url"] == "https://media.kijiji.ca/a.jpg"
    assert listing["seller_type"] == "dealer"


def test_private_sales_set_the_median_once_there_are_enough(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    private = [_listing("kijiji", f"p{i}", f"https://x/p{i}", 3000 + 100 * i) for i in range(5)]
    dealer = [_listing("kijiji", f"d{i}", f"https://x/d{i}", 7900) for i in range(3)]
    for x in private:
        x.seller_type = "private"
    for x in dealer:
        x.seller_type = "dealer"
    upsert_listings(private + dealer, db_path=db)

    snap = snapshot("crf150f", SqliteStore(db))
    by_id = {x["external_id"]: x for x in snap.listings}

    assert (snap.stats["sellers"], snap.stats["median"], snap.stats["count"]) == (
        "private",
        3200,
        5,
    )
    assert by_id["d0"]["rating"] == "HIGH"  # dealer price vs what private sellers ask
    assert by_id["p0"]["rating"] == "FAIR"
