"""Compose store reads with pure domain scoring."""

from datetime import UTC, datetime

from ..domain.scoring import (
    GOALS,
    MarketSnapshot,
    market_stats,
    project_stats,
    score_listings,
    trend,
    weekly_medians,
)
from .ports import ListingStore


def snapshot(slug: str, store: ListingStore, goal: str = "project") -> MarketSnapshot:
    """Fetch a model's market from the store and score it for the model's goal (models.yml)."""
    market = store.market_listings(slug, days=90)
    prices = [x["price"] for x in market]
    stats = market_stats(prices)
    if goal == "project":
        stats |= project_stats(market)
    drops = store.price_drops(slug)
    listings = [
        {
            **listing,
            "description": listing.get("full_description") or listing["description"],
            "price_drop_from": drops.get(listing["id"]),
        }
        for listing in store.active_listings(slug)
    ]
    return MarketSnapshot(
        slug=slug,
        stats=stats,
        listings=score_listings(listings, stats["median"], stats.get("project_median"), goal),
        trend=trend(
            [x["price"] for x in store.market_listings(slug, days=30)],
            [x["price"] for x in store.market_listings(slug, days=90, older_than_days=30)],
        ),
        prices=prices,
        sold=store.sold_listings(slug),
        weekly=weekly_medians(
            store.price_timelines(slug), now=datetime.now(UTC).replace(tzinfo=None)
        ),
    )


def model_goal(config: dict, slug: str) -> str:
    """The model's goal in models.yml (project when it isn't configured). ValueError if unknown."""
    goal = next(
        (e.get("goal", "project") for g in config.values() for e in g or [] if e["slug"] == slug),
        "project",
    )
    if goal not in GOALS:
        raise ValueError(f"{slug}: unknown goal {goal!r} in models.yml (use {', '.join(GOALS)})")
    return str(goal)
