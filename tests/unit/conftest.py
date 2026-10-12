import pytest

from dirtbike_tracker.domain.scoring import MarketSnapshot, market_stats, score_listings


@pytest.fixture
def snap() -> MarketSnapshot:
    prices = [1400.0, 2000.0, 2600.0]
    stats = market_stats(prices)
    listings = [
        {
            "price": p,
            "price_state": "numeric",
            "location": "Barrie, ON",
            "title": f"CRF150F at {p:.0f}",
            "url": f"https://example.com/{p:.0f}",
            "source": "kijiji",
            "external_id": f"{p:.0f}",
        }
        for p in prices
    ]
    listings.append({**listings[0], "price": None, "price_state": "please_contact"})
    return MarketSnapshot(
        slug="crf150f",
        stats=stats,
        listings=score_listings(listings, stats["median"]),
        trend=-12.5,
        prices=prices,
    )


@pytest.fixture(autouse=True)
def _no_live_db_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cli()` runs init_db() on every invocation; keep tests off the real (prod-shared) DB."""
    from dirtbike_tracker.application import cli as cli_module

    monkeypatch.setattr(cli_module, "init_db", lambda: None)
