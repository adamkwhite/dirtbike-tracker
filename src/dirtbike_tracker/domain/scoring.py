"""
Deal scoring: compute market medians and rate listings vs market.

A listing's deal rating is based on its price relative to the median
price of comparable listings. Pure: no I/O. Prices and listings are
fetched by infrastructure/storage.py and composed in application/market.py.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

RATING_DEAL = -0.30
RATING_GOOD = -0.10
RATING_FAIR = 0.15

URGENT_MAX_DAYS = 2  # fresh DEAL: someone else will grab it
NEGOTIABLE_MIN_DAYS = 14  # stale GOOD: seller may take less


@dataclass
class MarketSnapshot:
    """One model's scored market, as consumed by the CLI and renderers."""

    slug: str
    stats: dict
    listings: list[dict]
    trend: float | None
    prices: list[float]
    sold: list[dict] = field(default_factory=list)  # likely-sold, most recent first
    weekly: list[tuple[str, float | None]] = field(default_factory=list)  # (week start, median)


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


def urgency(rating: str, age_days: float | None) -> str | None:
    """URGENT for a fresh DEAL, NEGOTIABLE for a stale GOOD, else None."""
    if age_days is None:
        return None
    if rating == "DEAL" and age_days < URGENT_MAX_DAYS:
        return "URGENT"
    if rating == "GOOD" and age_days > NEGOTIABLE_MIN_DAYS:
        return "NEGOTIABLE"
    return None


def score_listings(listings: list[dict], median_price: float | None) -> list[dict]:
    """Attach rating and urgency to each listing, best deals first, unpriced last."""
    scored = []
    for listing in listings:
        rated = {**listing, **rate_listing(listing["price"], median_price)}
        rated["urgency"] = urgency(rated["rating"], listing.get("age_days"))
        scored.append(rated)
    scored.sort(key=lambda x: (x["delta_pct"] is None, x["delta_pct"] or 0))
    return scored


def weekly_medians(
    timelines: list[dict], now: datetime, weeks: int = 13
) -> list[tuple[str, float | None]]:
    """Market median for each of the last `weeks` 7-day windows, oldest first.

    timelines: {"start", "end": datetime, "history": [(datetime, price), ...] ascending}.
    A listing counts in a window if it was on the market at any point in it, at its
    latest price by the window's end. Windows with no priced listings are None.
    """
    out: list[tuple[str, float | None]] = []
    for i in range(weeks - 1, -1, -1):
        w_end = now - timedelta(days=7 * i)
        w_start = w_end - timedelta(days=7)
        prices = []
        for t in timelines:
            if t["start"] > w_end or t["end"] < w_start or not t["history"]:
                continue
            known = [p for at, p in t["history"] if at <= w_end]
            # ponytail: before our first observation, assume the first price we saw.
            # Kijiji post dates reach back months; without this the chart starts empty.
            price = known[-1] if known else t["history"][0][1]
            if price and price > 100:
                prices.append(price)
        out.append((w_start.date().isoformat(), median(prices) if prices else None))
    return out
