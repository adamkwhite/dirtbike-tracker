"""
Deal scoring: compute market medians and rate listings vs market.

A listing's deal rating is based on its price relative to the median
price of comparable listings. Pure: no I/O. Prices and listings are
fetched by infrastructure/storage.py and composed in application/market.py.
"""

from dataclasses import dataclass, field
from statistics import median

RATING_DEAL = -0.30
RATING_GOOD = -0.10
RATING_FAIR = 0.15


@dataclass
class MarketSnapshot:
    """One model's scored market, as consumed by the CLI and renderers."""

    slug: str
    stats: dict
    listings: list[dict]
    trend: float | None
    prices: list[float]
    sold: list[dict] = field(default_factory=list)  # likely-sold, most recent first


def market_stats(prices: list[float]) -> dict:
    """Return low/median/high/count for a set of market prices."""
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


def trend(recent: list[float], older: list[float]) -> float | None:
    """Percent change of the last-30-day median vs the 30-90-day median."""
    if not recent or not older:
        return None
    return round((median(recent) - median(older)) / median(older) * 100, 1)


def score_listings(listings: list[dict], median_price: float | None) -> list[dict]:
    """Attach a deal rating to each listing, best deals first, unpriced last."""
    scored = [{**listing, **rate_listing(listing["price"], median_price)} for listing in listings]
    scored.sort(key=lambda x: (x["delta_pct"] is None, x["delta_pct"] or 0))
    return scored
