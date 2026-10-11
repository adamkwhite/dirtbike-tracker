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
from datetime import datetime
from pathlib import Path

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
    now = datetime.utcnow().isoformat()

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
                    "UPDATE listings SET last_seen=?, is_active=1 WHERE id=?",
                    (now, listing_id),
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

            # Tag with every detected model (unique per listing)
            for slug in listing.model_slugs:
                conn.execute(
                    "INSERT OR IGNORE INTO listing_models (listing_id, model_slug) VALUES (?, ?)",
                    (listing_id, slug),
                )

    return stats


def mark_stale(cutoff_hours: int = 48, db_path: Path = DB_PATH) -> int:
    """Mark listings not seen recently as inactive. Returns count marked."""
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE listings SET is_active=0
            WHERE is_active=1
              AND julianday('now') - julianday(last_seen) > ?
            """,
            (cutoff_hours / 24.0,),
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
    """
    with connect(db_path) as conn:
        if model_slug:
            sql = """
                SELECT l.* FROM listings l
                JOIN listing_models m ON m.listing_id = l.id
                WHERE l.is_active=1 AND m.model_slug=?
            """
            params = [model_slug]
        else:
            sql = "SELECT * FROM listings WHERE is_active=1"
            params = []
        if primary_only:
            sql += " AND is_primary=1"
        sql += " ORDER BY last_seen DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
