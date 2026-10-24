"""Compose store reads with pure domain scoring."""

from datetime import UTC, datetime

from ..domain.scoring import (
    GOALS,
    MarketSnapshot,
    market_stats,
    private_market,
    project_stats,
    score_listings,
    trend,
    weekly_medians,
)
from .ports import ListingStore


def snapshot(slug: str, store: ListingStore, goal: str = "project") -> MarketSnapshot:
    """Fetch a model's market from the store and score it for the model's goal (models.yml)."""
    # rate against private sales once there are enough (#81): dealer prices run high
    market, seller_basis = private_market(store.market_listings(slug, days=90))
    prices = [x["price"] for x in market]
    stats = market_stats(prices) | {"sellers": seller_basis}
    if goal == "project":
        stats |= project_stats(market)
    sellers = {"private"} if seller_basis == "private" else None
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
        goal=goal,
        stats=stats,
        listings=score_listings(listings, stats["median"], stats.get("project_median"), goal),
        trend=trend(
            [x["price"] for x in _only(store.market_listings(slug, days=30), sellers)],
            [
                x["price"]
                for x in _only(store.market_listings(slug, days=90, older_than_days=30), sellers)
            ],
        ),
        prices=prices,
        sold=store.sold_listings(slug),
        hidden=store.hidden_listings(slug),
        weekly=weekly_medians(
            _only(store.price_timelines(slug), sellers), now=datetime.now(UTC).replace(tzinfo=None)
        ),
    )


def _only(rows: list[dict], sellers: set[str] | None) -> list[dict]:
    """Rows from these seller types (None = all), so trend and chart match the median."""
    return rows if sellers is None else [x for x in rows if x.get("seller_type") in sellers]


def model_make(config: dict, slug: str) -> str:
    """The model's make in models.yml ("Honda"), or "" when it isn't set."""
    return next(
        (str(e.get("make", "")) for g in config.values() for e in g or [] if e["slug"] == slug), ""
    )


def model_cc(config: dict, slug: str) -> int | None:
    """The model's engine size in models.yml, or None when it isn't set."""
    return next((e.get("cc") for g in config.values() for e in g or [] if e["slug"] == slug), None)


def model_goal(config: dict, slug: str) -> str:
    """The model's goal in models.yml (project when it isn't configured). ValueError if unknown."""
    goal = next(
        (e.get("goal", "project") for g in config.values() for e in g or [] if e["slug"] == slug),
        "project",
    )
    if goal not in GOALS:
        raise ValueError(f"{slug}: unknown goal {goal!r} in models.yml (use {', '.join(GOALS)})")
    return str(goal)
