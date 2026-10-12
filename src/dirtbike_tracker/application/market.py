"""Compose storage reads with pure domain scoring."""

from datetime import UTC, datetime
from pathlib import Path

from ..domain.scoring import (
    MarketSnapshot,
    market_stats,
    project_stats,
    score_listings,
    trend,
    weekly_medians,
)
from ..infrastructure.storage import (
    active_listings,
    market_listings,
    market_prices,
    price_drops,
    price_timelines,
    sold_listings,
)


def snapshot(slug: str, goal: str = "project", db_path: Path | None = None) -> MarketSnapshot:
    """Fetch a model's market from the DB and score it for the model's goal (models.yml)."""
    market = market_listings(slug, days=90, db_path=db_path)
    prices = [x["price"] for x in market]
    stats = market_stats(prices)
    if goal == "project":
        stats |= project_stats(market)
    drops = price_drops(slug, db_path=db_path)
    listings = [
        {
            **listing,
            "description": listing.get("full_description") or listing["description"],
            "price_drop_from": drops.get(listing["id"]),
        }
        for listing in active_listings(slug, primary_only=True, db_path=db_path)
    ]
    return MarketSnapshot(
        slug=slug,
        stats=stats,
        listings=score_listings(listings, stats["median"], stats.get("project_median"), goal),
        trend=trend(
            market_prices(slug, days=30, db_path=db_path),
            market_prices(slug, days=90, db_path=db_path, older_than_days=30),
        ),
        prices=prices,
        sold=sold_listings(slug, db_path=db_path),
        weekly=weekly_medians(
            price_timelines(slug, db_path=db_path), now=datetime.now(UTC).replace(tzinfo=None)
        ),
    )
