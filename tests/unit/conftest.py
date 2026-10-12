from pathlib import Path

import pytest

from dirtbike_tracker.application.ports import App
from dirtbike_tracker.domain.scoring import MarketSnapshot, market_stats, score_listings
from dirtbike_tracker.infrastructure.storage import SqliteStore, init_db


class PushSpy(list[dict]):
    """A Notifier that records each push and reports `ok` (False = the send failed)."""

    ok = True

    def __call__(
        self,
        title: str,
        body: str,
        priority: int = 3,
        tags: list[str] | None = None,
        click_url: str | None = None,
        attach_url: str | None = None,
    ) -> bool:
        self.append(
            {"title": title, "body": body, "priority": priority, "tags": tags}
            | {"click_url": click_url, "attach_url": attach_url}
        )
        return self.ok


@pytest.fixture
def sent() -> PushSpy:
    return PushSpy()


@pytest.fixture
def app(tmp_path: Path, sent: PushSpy) -> App:
    """An App on a fresh SQLite DB in tmp_path, no sources or fetchers, pushes to `sent`.

    Tests pass it to commands (CliRunner().invoke(cli, args, obj=app)) and override fields.
    """
    db = tmp_path / "listings.db"
    init_db(db)
    return App(
        store=SqliteStore(db),
        sources={},
        detail_fetchers={},
        notify=sent,
        config={"priority": [{"slug": "crf150f", "goal": "project"}]},
        state_dir=tmp_path / "state",
        dashboard_path=tmp_path / "dashboard.html",
        reports_dir=tmp_path / "reports",
        ping=lambda: True,
    )


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
