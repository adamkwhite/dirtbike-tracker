"""CLI commands, with snapshot() stubbed so no DB is touched."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.domain.scoring import MarketSnapshot, score_listings


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
    monkeypatch.setattr(cli_module, "mark_stale", lambda sources: 0)
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


def test_watch_does_not_alert_on_junk_price_under_target(
    stub_watch: list[set], snap: MarketSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap.listings = score_listings(
        [*snap.listings, {**snap.listings[0], "external_id": "plastics", "price": 50.0}],
        snap.stats["median"],
    )
    monkeypatch.setattr(
        cli_module, "load_config", lambda: {"p": [{"slug": "crf150f", "target_price": 1500}]}
    )
    alerted: list[str] = []

    def fake_notify_deal(listing: dict) -> bool:
        alerted.append(listing["external_id"])
        return True

    monkeypatch.setattr(cli_module, "notify_deal", fake_notify_deal)

    result = CliRunner().invoke(cli, ["watch"])

    assert result.exit_code == 0, result.output
    assert alerted == ["1400"]


def test_open_in_browser_hands_file_to_windows_under_wsl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(stdout="\\\\wsl.localhost\\Ubuntu\\d.html\n")

    monkeypatch.setattr(
        cli_module.platform, "uname", lambda: SimpleNamespace(release="6.6-microsoft-standard-WSL2")
    )
    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.webbrowser, "open", lambda _: pytest.fail("webbrowser used"))

    cli_module._open_in_browser(Path("/home/u/d.html"))

    assert calls == [
        ["wslpath", "-w", "/home/u/d.html"],
        ["explorer.exe", "\\\\wsl.localhost\\Ubuntu\\d.html"],
    ]


def test_open_in_browser_uses_webbrowser_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        cli_module.platform, "uname", lambda: SimpleNamespace(release="6.8.0-generic")
    )
    monkeypatch.setattr(cli_module.webbrowser, "open", opened.append)

    cli_module._open_in_browser(Path("/home/u/d.html"))

    assert opened == ["file:///home/u/d.html"]


def _rated(rating: str, price: float = 2000, drop_from: float | None = None) -> dict:
    return {
        "source": "kijiji",
        "external_id": "9",
        "rating": rating,
        "price": price,
        "price_drop_from": drop_from,
    }


@pytest.mark.parametrize(
    ("listing", "target", "expected"),
    [
        (_rated("GOOD", drop_from=2400), None, ("notify_good", "drop:kijiji:9:2000", "drops")),
        (_rated("DEAL", drop_from=2400), None, ("notify_deal", "drop:kijiji:9:2000", "drops")),
        (_rated("DEAL"), None, ("notify_deal", "kijiji:9", "deals")),
        (_rated("FAIR"), 2500, ("notify_deal", "kijiji:9", "target_hits")),
        (_rated("GOOD"), None, ("notify_good", "kijiji:9", "goods")),
        (_rated("FAIR", drop_from=2400), None, None),  # dropped, still not a deal
        (_rated("UNKNOWN", price=50), 1500, None),
    ],
)
def test_alert_for(listing: dict, target: float | None, expected: tuple | None) -> None:
    choice = cli_module._alert_for(listing, target)

    got = None if choice is None else (choice[0].__name__, choice[1], choice[2])
    assert got == expected


def test_watch_alerts_price_drop_on_already_notified_listing_once(
    stub_watch: list[set], snap: MarketSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap.listings = [{**snap.listings[0], "price_drop_from": 1800.0}]  # DEAL at 1400
    monkeypatch.setattr(cli_module, "_notified_ids", lambda: {"kijiji:1400"})
    alerted: list[str] = []

    def fake_notify_deal(listing: dict) -> bool:
        alerted.append(f"{listing['external_id']} was {listing['price_drop_from']}")
        return True

    monkeypatch.setattr(cli_module, "notify_deal", fake_notify_deal)

    first = CliRunner().invoke(cli, ["watch"])
    monkeypatch.setattr(cli_module, "_notified_ids", lambda: stub_watch[0])
    CliRunner().invoke(cli, ["watch"])

    assert first.exit_code == 0, first.output
    assert alerted == ["1400 was 1800.0"]
    assert "drop:kijiji:1400:1400" in stub_watch[0]
