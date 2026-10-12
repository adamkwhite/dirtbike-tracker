"""CLI commands, with snapshot() stubbed so no DB is touched."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.domain.scoring import MarketSnapshot


@pytest.fixture
def stub_snapshot(snap: MarketSnapshot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_module, "snapshot", lambda slug: snap)
    monkeypatch.setattr(cli_module, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cli_module, "DASHBOARD_PATH", tmp_path / "dashboard.html")


@pytest.mark.usefixtures("stub_snapshot")
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["list", "--model", "crf150f"], "4 active listings for crf150f"),
        (["stats", "--model", "crf150f"], "Median:    $2000"),
        (["report", "--model", "crf150f"], "# CRF150F market snapshot"),
        (["report", "--model", "crf150f", "--write"], "Report written to"),
        (["dashboard", "--models", "crf150f"], "Dashboard written"),
    ],
)
def test_commands_render_snapshot(args: list[str], expected: str) -> None:
    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 0, result.output
    assert expected in result.output


def test_cli_registers_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in ("scrape", "list", "stats", "report", "watch", "dashboard", "models"):
        assert command in result.output


@pytest.fixture
def stub_watch(snap: MarketSnapshot, monkeypatch: pytest.MonkeyPatch) -> list[set]:
    recorded: list[set] = []
    monkeypatch.setattr(cli_module, "load_config", lambda: {"priority": [{"slug": "crf150f"}]})
    monkeypatch.setattr(cli_module, "_scrape_one", lambda *a, **k: [])
    monkeypatch.setattr(cli_module, "upsert_listings", lambda _: {"new": 0, "updated": 0})
    monkeypatch.setattr(cli_module, "snapshot", lambda slug: snap)
    monkeypatch.setattr(cli_module, "_notified_ids", set)
    monkeypatch.setattr(cli_module, "_record_notified", recorded.append)
    monkeypatch.setattr(cli_module, "mark_stale", lambda: 0)
    monkeypatch.setattr(cli_module, "write_dashboard", lambda *a: None)
    monkeypatch.setattr(cli_module, "_check_source_health", lambda *a: None)
    monkeypatch.setattr(cli_module, "ping_healthcheck", lambda: True)
    monkeypatch.setattr(cli_module, "notify_good", lambda _: True)
    return recorded


def _raise_config_error(_: dict) -> bool:
    raise cli_module.NotifyConfigError("no topic")


@pytest.mark.parametrize(
    ("args", "notify_deal", "deal_recorded"),
    [
        ([], lambda _: True, True),
        ([], lambda _: False, False),  # HTTP failure: retry next run
        ([], _raise_config_error, False),  # missing NTFY_TOPIC: retry next run
        (["--no-notify"], _raise_config_error, True),  # silenced on purpose
    ],
)
def test_watch_records_deal_only_when_alert_sent(
    stub_watch: list[set],
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    notify_deal: object,
    deal_recorded: bool,
) -> None:
    monkeypatch.setattr(cli_module, "notify_deal", notify_deal)

    result = CliRunner().invoke(cli, ["watch", *args])

    assert result.exit_code == 0, result.output
    assert ("kijiji:1400" in stub_watch[0]) is deal_recorded
