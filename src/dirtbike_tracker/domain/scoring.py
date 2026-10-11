"""
Deal scoring: compute market medians and rate listings vs market.

A listing's deal rating is based on its price relative to the median
price of comparable listings in the DB. Uses the listing_models join
table so a package listing counts toward every model it contains.
"""

from datetime import datetime, timedelta
from statistics import median

from ..infrastructure.storage import DB_PATH, connect

RATING_DEAL = -0.30
RATING_GOOD = -0.10
RATING_FAIR = 0.15


def _market_prices(model_slug: str, days: int = 90, db_path=DB_PATH) -> list[float]:
    """All non-null prices for a model in the last N days (primary listings only)."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT l.price FROM listings l
            JOIN listing_models m ON m.listing_id = l.id
            WHERE m.model_slug=?
              AND l.price IS NOT NULL
              AND l.price > 100
              AND l.first_seen >= ?
              AND l.is_primary = 1
            """,
            (model_slug, cutoff),
        ).fetchall()
    return [r["price"] for r in rows]


def market_stats(model_slug: str, days: int = 90, db_path=DB_PATH) -> dict:
    """Return low/median/high/count for a model's recent market."""
    prices = _market_prices(model_slug, days, db_path)
    if not prices:
        return {"low": None, "median": None, "high": None, "count": 0}
    return {
        "low": min(prices),
        "median": median(prices),
        "high": max(prices),
        "count": len(prices),
    }


def rate_listing(price: float | None, median_price: float | None) -> dict:
    """Rate one listing's price against the market median."""
    if price is None or median_price is None or median_price == 0:
        return {"rating": "UNKNOWN", "delta_pct": None}

    delta = (price - median_price) / median_price
    if delta <= RATING_DEAL:
        rating = "DEAL"
    elif delta <= RATING_GOOD:
        rating = "GOOD"
    elif delta <= RATING_FAIR:
        rating = "FAIR"
    else:
        rating = "HIGH"

    return {"rating": rating, "delta_pct": round(delta * 100, 1)}


def trend(model_slug: str, db_path=DB_PATH) -> float | None:
    """Compare median price in last 30 days vs 30-90 days ago."""
    recent = _market_prices(model_slug, days=30, db_path=db_path)
    older = [p for p in _market_prices(model_slug, days=90, db_path=db_path) if p not in recent]
    if not recent or not older:
        return None
    return round((median(recent) - median(older)) / median(older) * 100, 1)


def score_active(model_slug: str, db_path=DB_PATH) -> list[dict]:
    """Return all active primary listings for a model, each with a deal rating."""
    from storage import active_listings

    stats = market_stats(model_slug, db_path=db_path)
    med = stats["median"]
    scored = []
    for listing in active_listings(model_slug, primary_only=True, db_path=db_path):
        rating = rate_listing(listing["price"], med)
        scored.append({**listing, **rating})
    scored.sort(key=lambda x: (x["delta_pct"] is None, x["delta_pct"] or 0))
    return scored
