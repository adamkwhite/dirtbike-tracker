"""
SQLite persistence for Kijiji dirt bike listings.

Dedupes by (source, external_id), tracks first/last seen, logs price changes.
Supports multi-model tagging via a separate listing_models join table.
Also deduplicates cross-posts (same bike posted under multiple URLs) by
grouping on a (title, price, location) fingerprint.
"""

import hashlib
import re
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..domain.scoring import MIN_BIKE_PRICE
from .kijiji_scraper import Listing

DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "listings.db"


SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    price REAL,
    price_state TEXT DEFAULT 'numeric',
    location TEXT,
    posted_text TEXT,
    description TEXT,
    fingerprint TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    is_primary INTEGER DEFAULT 1,
    sold_at TEXT,
    dismissed_at TEXT,
    full_description TEXT,
    detail_fetched_at TEXT,
    contacted_at TEXT,
    contact_note TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS listing_models (
    listing_id INTEGER NOT NULL,
    model_slug TEXT NOT NULL,
    PRIMARY KEY(listing_id, model_slug),
    FOREIGN KEY(listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    price REAL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(listing_id) REFERENCES listings(id)
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_fingerprint ON listings(fingerprint);
CREATE INDEX IF NOT EXISTS idx_active ON listings(is_active);
CREATE INDEX IF NOT EXISTS idx_primary ON listings(is_primary);
CREATE INDEX IF NOT EXISTS idx_lm_model ON listing_models(model_slug);
"""


@contextmanager
def connect(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    # Order matters: tables first, then migrate old schemas to add new
    # columns (fingerprint, price_state, is_primary) before building indexes
    # that reference them. An existing DB from Weekend 1 is missing those
    # columns and the index creation would fail without the migration step.
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_TABLES)
    _migrate_old_schema(db_path)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_INDEXES)


def _migrate_old_schema(db_path: Path = DB_PATH) -> None:
    """Add new columns to pre-Weekend-2 databases without destroying data."""
    with connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)").fetchall()}
        if "price_state" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN price_state TEXT DEFAULT 'numeric'")
        if "fingerprint" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN fingerprint TEXT")
        if "is_primary" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN is_primary INTEGER DEFAULT 1")
        if "sold_at" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN sold_at TEXT")
        if "dismissed_at" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN dismissed_at TEXT")
        if "full_description" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN full_description TEXT")
            conn.execute("ALTER TABLE listings ADD COLUMN detail_fetched_at TEXT")
        if "contacted_at" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN contacted_at TEXT")
            conn.execute("ALTER TABLE listings ADD COLUMN contact_note TEXT")


def _compute_fingerprint(title: str, price: float | None, location: str) -> str:
    """Hash a (title, price, location) tuple for cross-post dedup."""
    normalized_title = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    price_bucket = int(price // 50) if price else -1
    normalized_loc = re.sub(r"[^a-z0-9]", "", (location or "").lower())[:12]
    key = f"{normalized_title}|{price_bucket}|{normalized_loc}"
    return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:16]


def upsert_listings(listings: Iterable[Listing], db_path: Path = DB_PATH) -> dict:
    """Insert new listings, log price changes, dedup cross-posts, tag with all models."""
    stats = {"new": 0, "updated": 0, "price_changed": 0, "cross_posts": 0}
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()

    with connect(db_path) as conn:
        for listing in listings:
            fingerprint = _compute_fingerprint(listing.title, listing.price, listing.location)

            existing = conn.execute(
                "SELECT id, price FROM listings WHERE source=? AND external_id=?",
                (listing.source, listing.external_id),
            ).fetchone()

            if existing is None:
                # Mark as a cross-post (non-primary) if another listing shares this fingerprint
                is_primary = 1
                prior = conn.execute(
                    "SELECT id FROM listings WHERE fingerprint=? AND is_active=1 LIMIT 1",
                    (fingerprint,),
                ).fetchone()
                if prior is not None:
                    is_primary = 0
                    stats["cross_posts"] += 1

                cursor = conn.execute(
                    """
                    INSERT INTO listings
                    (source, external_id, url, title, price, price_state, location,
                     posted_text, description, fingerprint, first_seen, last_seen, is_primary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        listing.source,
                        listing.external_id,
                        listing.url,
                        listing.title,
                        listing.price,
                        listing.price_state,
                        listing.location,
                        listing.posted_text,
                        listing.description,
                        fingerprint,
                        now,
                        now,
                        is_primary,
                    ),
                )
                listing_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO price_history (listing_id, price, observed_at) VALUES (?, ?, ?)",
                    (listing_id, listing.price, now),
                )
                stats["new"] += 1
            else:
                listing_id = existing["id"]
                conn.execute(
                    # reappearing clears a likely-sold tag (relisted, or a false positive)
                    "UPDATE listings SET last_seen=?, is_active=1, sold_at=NULL,"
                    " posted_text=COALESCE(NULLIF(?, ''), posted_text),"
                    " description=COALESCE(NULLIF(?, ''), description) WHERE id=?",
                    (now, listing.posted_text, listing.description, listing_id),
                )
                stats["updated"] += 1
                if existing["price"] != listing.price:
                    conn.execute(
                        "UPDATE listings SET price=? WHERE id=?",
                        (listing.price, listing_id),
                    )
                    conn.execute(
                        "INSERT INTO price_history (listing_id, price, observed_at) VALUES (?, ?, ?)",
                        (listing_id, listing.price, now),
                    )
                    stats["price_changed"] += 1

            if listing.full_description:
                # The search page already had the whole ad (#76): no detail fetch needed.
                # Never overwrites a body #71 fetched.
                conn.execute(
                    "UPDATE listings SET full_description=?, detail_fetched_at=?"
                    " WHERE id=? AND detail_fetched_at IS NULL",
                    (listing.full_description, now, listing_id),
                )

            # Tag with every detected model (unique per listing)
            for slug in listing.model_slugs:
                conn.execute(
                    "INSERT OR IGNORE INTO listing_models (listing_id, model_slug) VALUES (?, ?)",
                    (listing_id, slug),
                )

    return stats


def mark_stale(
    sources: Iterable[str],
    cutoff_hours: int = 48,
    sold_after_hours: int = 72,
    db_path: Path = DB_PATH,
) -> int:
    """Age out listings from `sources` not seen recently. Returns count marked inactive.

    Unseen for `cutoff_hours` -> inactive; for `sold_after_hours` -> likely sold
    (sold_at = last_seen, the best estimate of when it went). Pass only sources
    that returned results this run: a broken scraper sees nothing, and would
    otherwise tag its whole inventory as sold.
    """
    # ponytail: scrape_model caps at 3 pages; a model with more results than that
    # would see its oldest listings fall off and read as sold. Fine at current volumes.
    sources = list(sources)
    if not sources:
        return 0
    marks = ",".join("?" * len(sources))
    with connect(db_path) as conn:
        cursor = conn.execute(
            f"""
            UPDATE listings SET is_active=0
            WHERE is_active=1 AND source IN ({marks})
              AND julianday('now') - julianday(last_seen) > ?
            """,  # only "?" placeholders are interpolated
            (*sources, cutoff_hours / 24.0),
        )
        conn.execute(
            f"""
            UPDATE listings SET sold_at=last_seen
            WHERE sold_at IS NULL AND source IN ({marks})
              AND julianday('now') - julianday(last_seen) > ?
            """,  # only "?" placeholders are interpolated
            (*sources, sold_after_hours / 24.0),
        )
        return int(cursor.rowcount)


def active_listings(
    model_slug: str | None = None,
    primary_only: bool = True,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return active listings, optionally filtered by model and primary-only.

    primary_only=True hides cross-posted duplicates (keeps the first-seen copy).
    Dismissed listings are always excluded (they still count toward the market).
    age_days counts from the source's post time when known (Kijiji's
    activationDate in posted_text), else from when we first saw it.
    """
    age = (
        "julianday('now') - julianday(COALESCE(NULLIF(posted_text, ''), first_seen)) AS age_days,"
        " julianday('now') - julianday(contacted_at) AS contacted_days"
    )
    with connect(db_path) as conn:
        if model_slug:
            sql = f"""
                SELECT l.*, {age} FROM listings l
                JOIN listing_models m ON m.listing_id = l.id
                WHERE l.is_active=1 AND l.dismissed_at IS NULL AND m.model_slug=?
            """
            params = [model_slug]
        else:
            sql = f"SELECT *, {age} FROM listings WHERE is_active=1 AND dismissed_at IS NULL"
            params = []
        if primary_only:
            sql += " AND is_primary=1"
        sql += " ORDER BY last_seen DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def market_prices(
    model_slug: str, days: int = 90, db_path: Path = DB_PATH, older_than_days: int = 0
) -> list[float]:
    """One price per primary listing first seen between `days` and `older_than_days` ago."""
    return [x["price"] for x in market_listings(model_slug, days, db_path, older_than_days)]


def market_listings(
    model_slug: str, days: int = 90, db_path: Path = DB_PATH, older_than_days: int = 0
) -> list[dict]:
    """market_prices' listings, with the text needed to tell project bikes apart (#72)."""
    # naive UTC to match how first_seen is stored
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = (now - timedelta(days=days)).isoformat()
    newest = (now - timedelta(days=older_than_days)).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT l.price, l.title, COALESCE(l.full_description, l.description) AS description
            FROM listings l
            JOIN listing_models m ON m.listing_id = l.id
            WHERE m.model_slug=?
              AND l.price IS NOT NULL
              AND l.price > ?
              AND l.first_seen >= ?
              AND l.first_seen <= ?
              AND l.is_primary = 1
            """,
            (model_slug, MIN_BIKE_PRICE, cutoff, newest),
        ).fetchall()
    return [dict(r) for r in rows]


def sold_listings(
    model_slug: str, days: int = 90, limit: int = 5, db_path: Path = DB_PATH
) -> list[dict]:
    """Most recent likely-sold primary listings for a model, with days on market."""
    cutoff = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT l.*, CAST(julianday(l.last_seen) - julianday(l.first_seen) AS INTEGER)
                   AS days_listed
            FROM listings l
            JOIN listing_models m ON m.listing_id = l.id
            WHERE m.model_slug=? AND l.sold_at >= ? AND l.is_primary=1
            ORDER BY l.sold_at DESC
            LIMIT ?
            """,
            (model_slug, cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def price_timelines(model_slug: str, days: int = 91, db_path: Path = DB_PATH) -> list[dict]:
    """On-market span and price observations per primary listing seen in the last N days.

    start = source post date (Kijiji activationDate) when known, else first_seen.
    SQLite datetime() normalizes both formats to naive UTC.
    """
    cutoff = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT l.id,
                   datetime(COALESCE(NULLIF(l.posted_text, ''), l.first_seen)) AS start,
                   datetime(l.last_seen) AS end,
                   datetime(h.observed_at) AS at,
                   h.price
            FROM listings l
            JOIN listing_models m ON m.listing_id = l.id
            JOIN price_history h ON h.listing_id = l.id
            WHERE m.model_slug=? AND l.is_primary=1 AND l.last_seen >= ?
            ORDER BY l.id, h.observed_at
            """,
            (model_slug, cutoff),
        ).fetchall()
    timelines: dict[int, dict] = {}
    for r in rows:
        t = timelines.setdefault(
            r["id"],
            {
                "start": datetime.fromisoformat(r["start"]),
                "end": datetime.fromisoformat(r["end"]),
                "history": [],
            },
        )
        t["history"].append((datetime.fromisoformat(r["at"]), r["price"]))
    return list(timelines.values())


def price_drops(model_slug: str, db_path: Path = DB_PATH) -> dict[int, float]:
    """{listing id: previous price} for listings whose latest price is below the one before.

    Read from price_history rather than flagged at upsert time, so a drop whose
    alert failed to send is still visible (and retried) on the next run.
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT h.listing_id, h.prev FROM (
                SELECT listing_id, price,
                       LAG(price) OVER (PARTITION BY listing_id ORDER BY observed_at) AS prev,
                       ROW_NUMBER() OVER (PARTITION BY listing_id ORDER BY observed_at DESC) AS rn
                FROM price_history
            ) h
            JOIN listing_models m ON m.listing_id = h.listing_id
            WHERE m.model_slug=? AND h.rn=1 AND h.prev IS NOT NULL AND h.price < h.prev
            """,
            (model_slug,),
        ).fetchall()
    return {r["listing_id"]: r["prev"] for r in rows}


def save_full_description(listing_id: int, text: str, db_path: Path = DB_PATH) -> None:
    """Store the full ad text. upsert never touches it, so card snippets can't clobber it."""
    stamp = datetime.now(UTC).replace(tzinfo=None).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE listings SET full_description=?, detail_fetched_at=? WHERE id=?",
            (text, stamp, listing_id),
        )


def set_dismissed(ref: str, dismissed: bool, db_path: Path = DB_PATH) -> list[str]:
    """Dismiss (or restore) the listing matching an external id or listing URL.

    Persists across scrapes: upsert never touches dismissed_at. Returns the
    titles of matched listings (empty if nothing matched).
    """
    stamp = datetime.now(UTC).replace(tzinfo=None).isoformat() if dismissed else None
    with connect(db_path) as conn:
        rows = _match_listings(conn, ref)
        conn.executemany(
            "UPDATE listings SET dismissed_at=? WHERE id=?", [(stamp, r["id"]) for r in rows]
        )
    return [r["title"] for r in rows]


def set_contact(
    ref: str, at: datetime | None, note: str | None = None, db_path: Path = DB_PATH
) -> list[str]:
    """Log contacting a listing's seller (#23), or clear it with at=None.

    A new note replaces the old one; no note keeps it. upsert never touches these
    columns. Returns the titles of matched listings (empty if nothing matched).
    """
    stamp = at.isoformat() if at else None
    with connect(db_path) as conn:
        rows = _match_listings(conn, ref)
        conn.executemany(
            "UPDATE listings SET contacted_at=?,"
            " contact_note=CASE WHEN ? IS NULL THEN NULL ELSE COALESCE(?, contact_note) END"
            " WHERE id=?",
            [(stamp, stamp, note, r["id"]) for r in rows],
        )
    return [r["title"] for r in rows]


def _match_listings(conn: sqlite3.Connection, ref: str) -> list[sqlite3.Row]:
    """Listings matching an external id or a listing URL (query string and fragment ignored)."""
    ref = ref.strip().split("?")[0].split("#")[0].rstrip("/")
    # external ids are 10-digit Kijiji ids or AutoTrader UUIDs, which end their URLs
    return conn.execute(
        "SELECT id, title FROM listings WHERE external_id=? OR ? LIKE '%' || external_id",
        (ref, ref),
    ).fetchall()
