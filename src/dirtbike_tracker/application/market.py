"""Compose storage reads with pure domain scoring."""

from pathlib import Path

from ..domain.scoring import MarketSnapshot, market_stats, score_listings, trend
from ..infrastructure.storage import DB_PATH, active_listings, market_prices


def snapshot(slug: str, db_path: Path = DB_PATH) -> MarketSnapshot:
    """Fetch a model's market from the DB and score it."""
    prices = market_prices(slug, days=90, db_path=db_path)
    stats = market_stats(prices)
    listings = active_listings(slug, primary_only=True, db_path=db_path)
    return MarketSnapshot(
        slug=slug,
        stats=stats,
        listings=score_listings(listings, stats["median"]),
        trend=trend(market_prices(slug, days=30, db_path=db_path), prices),
        prices=prices,
    )
