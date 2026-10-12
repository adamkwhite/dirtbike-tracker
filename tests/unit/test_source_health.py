from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import _check_source_health, _stale_sources

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def test_quiet_source_alerts_after_24h_then_at_most_daily() -> None:
    health: dict = {}
    quiet = Counter({"autotrader": 0})

    assert _stale_sources(health, quiet, T0) == []  # first sighting starts the clock
    assert _stale_sources(health, quiet, T0 + timedelta(hours=23)) == []
    assert _stale_sources(health, quiet, T0 + timedelta(hours=24)) == ["autotrader"]
    assert _stale_sources(health, quiet, T0 + timedelta(hours=30)) == []  # already alerted
    assert _stale_sources(health, quiet, T0 + timedelta(hours=48)) == ["autotrader"]


def test_results_reset_the_clock() -> None:
    health: dict = {}
    _stale_sources(health, Counter({"kijiji": 0}), T0)

    _stale_sources(health, Counter({"kijiji": 12}), T0 + timedelta(hours=20))

    assert _stale_sources(health, Counter({"kijiji": 0}), T0 + timedelta(hours=30)) == []
    assert health["kijiji"]["last_ok"] == (T0 + timedelta(hours=20)).isoformat()


def test_check_source_health_persists_and_notifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(cli_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli_module, "notify", lambda title, **_: sent.append(title))
    day_ago = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    (tmp_path / "source_health.json").write_text(
        f'{{"autotrader": {{"last_ok": "{day_ago}", "last_alert": null}}}}'
    )

    _check_source_health(Counter({"autotrader": 0, "kijiji": 9}), notify_enabled=True)

    assert sent == ["autotrader scraper returned nothing for 24h"]
    assert '"kijiji"' in (tmp_path / "source_health.json").read_text()
