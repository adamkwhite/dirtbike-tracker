"""snapshot() against a real SQLite DB in tmp_path."""

from datetime import datetime, timedelta
from pathlib import Path

from dirtbike_tracker.application.market import snapshot
from dirtbike_tracker.infrastructure.storage import connect, init_db


def _add(db: Path, ext_id: str, price: float | None, days_ago: int, **cols: int) -> None:
    seen = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO listings (source, external_id, url, title, price, first_seen, last_seen,"
            " is_active, is_primary) VALUES ('kijiji', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
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
    _add(db, "junk", 50, days_ago=5)  # <= $100 ignored for market

    snap = snapshot("crf150f", db_path=db)

    assert snap.stats == {"low": 1400, "median": 2000, "high": 2600, "count": 3}
    assert snap.trend == -39.1  # recent 1400 vs older median 2300
    assert snap.listings[0]["external_id"] == "junk"  # -97.5%, best "deal" first
    assert {s["external_id"] for s in snap.listings} == {
        "cheap",
        "mid",
        "pricey",
        "ancient",
        "junk",
    }
