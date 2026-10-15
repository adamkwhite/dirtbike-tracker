"""Cross-post dedup (#101): a dealer bike listed on several sites is one primary listing."""

import sqlite3
from contextlib import closing
from pathlib import Path

from dirtbike_tracker.domain.listing import Listing
from dirtbike_tracker.infrastructure.storage import (
    _compute_fingerprint,
    active_listings,
    init_db,
    upsert_listings,
)


def _bike(source: str, ext_id: str, title: str, location: str, seller: str | None) -> Listing:
    return Listing(
        source=source,
        external_id=ext_id,
        url=f"https://{source}.example/{ext_id}",
        title=title,
        price=24195.0,
        price_state="numeric",
        location=location,
        posted_text="",
        description="",
        model_slugs=["speedtriple"],
        scraped_at="",
        seller_type=seller,
    )


def test_a_dealer_bike_on_three_sites_is_one_primary_listing(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    title = "2026 Triumph Speed Triple 1200 RS Jet Black"

    stats = upsert_listings(
        [
            _bike("kijiji", "1", title, "Oshawa / Durham Region", "dealer"),
            _bike("autotrader", "2", title, "WHITBY", "dealer"),
            _bike(
                "sturgess",
                "3",
                "2026 Triumph Speed Triple 1200 RS Jet/Black",
                "Hamilton, ON",
                "dealer",
            ),
        ],
        db_path=db,
    )

    assert stats["cross_posts"] == 2
    assert [x["external_id"] for x in active_listings("speedtriple", db_path=db)] == ["1"]


def test_private_sellers_in_different_places_stay_separate(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    title = "2019 Honda Rebel 500"

    upsert_listings(
        [
            _bike("kijiji", "1", title, "Barrie", "private"),
            _bike("kijiji", "2", title, "Ottawa", "private"),
        ],
        db_path=db,
    )

    assert len(active_listings("speedtriple", db_path=db)) == 2


def test_migration_demotes_dealer_cross_posts_already_stored(tmp_path: Path) -> None:
    db = tmp_path / "listings.db"
    init_db(db)
    title = "2026 Triumph Speed Triple 1200 RS Jet Black"
    upsert_listings([_bike("kijiji", "1", title, "Oshawa / Durham Region", "dealer")], db_path=db)
    upsert_listings([_bike("autotrader", "2", title, "WHITBY", "dealer")], db_path=db)
    # as stored before #101: location in the key, both primary
    with closing(sqlite3.connect(db)) as conn, conn:
        old = _compute_fingerprint(title, 24195.0, "WHITBY")
        conn.execute(
            "UPDATE listings SET fingerprint=?, is_primary=1 WHERE external_id='2'", (old,)
        )
        conn.execute("PRAGMA user_version = 0")

    init_db(db)
    init_db(db)  # runs once: user_version is now 1

    assert [x["external_id"] for x in active_listings("speedtriple", db_path=db)] == ["1"]
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
