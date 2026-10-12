from collections import Counter
from datetime import UTC, datetime, timedelta

from dirtbike_tracker.application.ports import App
from dirtbike_tracker.application.watch import check_source_health, stale_sources

from .conftest import PushSpy

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def test_quiet_source_alerts_after_24h_then_at_most_daily() -> None:
    health: dict = {}
    quiet = Counter({"autotrader": 0})

    assert stale_sources(health, quiet, T0) == []  # first sighting starts the clock
    assert stale_sources(health, quiet, T0 + timedelta(hours=23)) == []
    assert stale_sources(health, quiet, T0 + timedelta(hours=24)) == ["autotrader"]
    assert stale_sources(health, quiet, T0 + timedelta(hours=30)) == []  # already alerted
    assert stale_sources(health, quiet, T0 + timedelta(hours=48)) == ["autotrader"]


def test_results_reset_the_clock() -> None:
    health: dict = {}
    stale_sources(health, Counter({"kijiji": 0}), T0)

    stale_sources(health, Counter({"kijiji": 12}), T0 + timedelta(hours=20))

    assert stale_sources(health, Counter({"kijiji": 0}), T0 + timedelta(hours=30)) == []
    assert health["kijiji"]["last_ok"] == (T0 + timedelta(hours=20)).isoformat()


def test_check_source_health_persists_and_notifies(app: App, sent: PushSpy) -> None:
    day_ago = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    app.state_dir.mkdir()
    (app.state_dir / "source_health.json").write_text(
        f'{{"autotrader": {{"last_ok": "{day_ago}", "last_alert": null}}}}'
    )

    warnings = check_source_health(app, Counter({"autotrader": 0, "kijiji": 9}), notify=True)

    assert [p["title"] for p in sent] == ["autotrader scraper returned nothing for 24h"]
    assert warnings[0].startswith("autotrader returned 0 listings for every model since ")
    assert '"kijiji"' in (app.state_dir / "source_health.json").read_text()
