"""CLI commands against an App of fakes (tests/unit/conftest.py): a tmp SQLite store,
fake sources and detail fetchers, a spy notifier. Nothing is patched except the OS
edge in the open-in-browser tests."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import cli, skip_unconfigured
from dirtbike_tracker.application.market import model_goal
from dirtbike_tracker.application.ports import App
from dirtbike_tracker.application.watch import alert_for
from dirtbike_tracker.domain.listing import Listing
from dirtbike_tracker.domain.scoring import GOALS
from dirtbike_tracker.infrastructure.kijiji_scraper import KNOWN_MODEL_SLUGS
from dirtbike_tracker.infrastructure.notifier import NotifyConfigError

from .conftest import PushSpy


def _listing(ext_id: str, price: float | None) -> Listing:
    return Listing(
        source="kijiji",
        external_id=ext_id,
        url=f"https://www.kijiji.ca/v-dirt-bikes-motocross/barrie/crf150f/{ext_id}",
        title=f"2008 Honda CRF150F {ext_id}",
        price=price,
        price_state="numeric" if price else "please_contact",
        location="Barrie, ON",
        posted_text="",
        description="",
        model_slugs=["crf150f"],
        scraped_at="",
    )


# median 2000: the 1400 bike is a DEAL (-30%), the rest FAIR/HIGH, one has no price
MARKET = [_listing("a1400", 1400), _listing("b2000", 2000), _listing("c2600", 2600)]
MARKET.append(_listing("d0", None))


def _invoke(app: App, *args: str) -> str:
    result = CliRunner().invoke(cli, list(args), obj=app)
    assert result.exit_code == 0, result.output
    return str(result.output)


@pytest.fixture
def market_app(app: App) -> App:
    """The App with Kijiji serving MARKET, already scraped into the store once."""
    app = replace(app, sources={"kijiji": lambda *_, **__: list(MARKET)})
    app.store.upsert_listings(MARKET)
    return app


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["list", "--model", "crf150f"], "4 active listings for crf150f"),
        (["stats", "--model", "crf150f"], "Median:    $2000"),
        (["report", "--model", "crf150f"], "# CRF150F market snapshot"),
        (["report", "--model", "crf150f", "--write"], "Report written to"),
        (["dashboard", "--models", "crf150f"], "Dashboard written"),
        (["models"], "crf150f"),
    ],
)
def test_commands_render_the_market(market_app: App, args: list[str], expected: str) -> None:
    assert expected in _invoke(market_app, *args)


def test_cli_registers_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in ("scrape", "list", "stats", "report", "watch", "dashboard", "models"):
        assert command in result.output


def test_scrape_stores_what_the_source_returns(market_app: App) -> None:
    output = _invoke(market_app, "scrape", "--model", "crf150f")

    assert "kijiji: 4 listings" in output
    assert len(market_app.store.active_listings("crf150f")) == 4


@pytest.mark.parametrize(
    ("ok", "args", "recorded"),
    [(True, [], True), (False, [], False), (False, ["--no-notify"], True)],
)
def test_watch_records_deal_only_when_alert_sent(
    market_app: App, sent: PushSpy, ok: bool, args: list[str], recorded: bool
) -> None:
    sent.ok = ok  # False: the send failed, so it retries next run

    _invoke(market_app, "watch", *args)

    notified = (market_app.state_dir / "notified_ids.txt").read_text().splitlines()
    assert ("kijiji:a1400" in notified) is recorded
    assert len(sent) == (0 if args else 1)


def test_a_quiet_watch_lists_the_alerts_it_skips(market_app: App, sent: PushSpy) -> None:
    output = _invoke(market_app, "watch", "--no-notify")

    assert sent == []
    assert "quiet deals: crf150f $1400" in output
    assert "a1400" in output


def test_watch_does_not_alert_on_junk_price_under_target(market_app: App, sent: PushSpy) -> None:
    plastics = _listing("plastics", 50)
    app = replace(
        market_app,
        sources={"kijiji": lambda *_, **__: [*MARKET, plastics]},
        config={"p": [{"slug": "crf150f", "goal": "project", "target_price": 1500}]},
    )

    _invoke(app, "watch")

    assert [p["title"] for p in sent] == ["URGENT DEAL: CRF150F $1400 (-30% vs median)"]  # fresh


def test_watch_reads_the_ad_and_a_red_flag_stops_the_alert(market_app: App, sent: PushSpy) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url.rsplit("/", 1)[-1])
        return "Runs, but no ownership."

    _invoke(replace(market_app, detail_fetchers={"kijiji": fetch}), "watch")

    assert fetched == ["a1400"]  # only the DEAL was worth reading
    assert sent == []


def test_watch_pings_and_writes_the_dashboard(market_app: App) -> None:
    pings: list[bool] = []

    def ping() -> bool:
        pings.append(True)
        return True

    _invoke(replace(market_app, ping=ping), "watch")

    assert pings == [True]
    assert market_app.dashboard_path.exists()


def test_skip_unconfigured_reports_instead_of_raising(capsys: pytest.CaptureFixture[str]) -> None:
    def no_topic(title: str, body: str, **_: object) -> bool:
        raise NotifyConfigError("no topic")

    assert skip_unconfigured(no_topic)("t", "b") is False  # type: ignore[arg-type]
    assert "(notify skipped: no topic)" in capsys.readouterr().out


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
        ({**_rated("DEAL"), "fit": 2}, 2500, None),  # red-flagged: no alert at any price
        ({**_rated("DEAL"), "fit": 5}, None, ("notify_deal", "kijiji:9", "deals")),
    ],
)
def test_alert_for(listing: dict, target: float | None, expected: tuple | None) -> None:
    choice = alert_for(listing, target)

    got = None if choice is None else (choice[0].__name__, choice[1], choice[2])
    assert got == expected


def test_dismiss_and_undismiss(market_app: App) -> None:
    assert "Dismissed: 2008 Honda CRF150F a1400" in _invoke(market_app, "dismiss", "a1400")
    assert len(market_app.store.active_listings("crf150f")) == 3

    assert "Restored: 2008 Honda CRF150F a1400" in _invoke(market_app, "undismiss", "a1400")
    assert len(market_app.store.active_listings("crf150f")) == 4


def test_dismiss_unknown_listing_exits_nonzero(app: App) -> None:
    result = CliRunner().invoke(cli, ["dismiss", "nope"], obj=app)

    assert result.exit_code == 1
    assert "No listing matches 'nope'" in result.output


def test_contact_logs_date_and_note_then_clears(market_app: App) -> None:
    url = "https://www.kijiji.ca/v-dirt-bikes-motocross/barrie/crf150f/a1400?utm=x"

    out = _invoke(market_app, "contact", url, "--on", "2026-10-01", "--note", "offered 750")
    (bike,) = [
        x for x in market_app.store.active_listings("crf150f") if x["external_id"] == "a1400"
    ]
    cleared = _invoke(market_app, "contact", "a1400", "--clear")
    missing = CliRunner().invoke(cli, ["contact", "nope"], obj=market_app)

    assert "Contacted: 2008 Honda CRF150F a1400" in out
    assert (bike["contacted_at"], bike["contact_note"]) == ("2026-10-01T00:00:00", "offered 750")
    assert "Cleared contact" in cleared
    assert missing.exit_code == 1


def test_model_goal_from_config() -> None:
    config = {"street": [{"slug": "rebel500", "goal": "ride"}], "x": None}

    assert model_goal(config, "rebel500") == "ride"
    assert model_goal(config, "crf150f") == "project"  # not configured


def test_unknown_goal_is_a_clear_error(app: App) -> None:
    app = replace(app, config={"g": [{"slug": "rebel500", "goal": "tour"}]})

    result = CliRunner().invoke(cli, ["stats", "--model", "rebel500"], obj=app)

    assert result.exit_code == 1
    assert "unknown goal 'tour'" in result.output


def test_models_yml_entries_are_valid() -> None:
    entries = [e for group in cli_module.load_config().values() for e in group or []]
    slugs = [e["slug"] for e in entries]

    assert len(slugs) == len(set(slugs))
    for e in entries:
        assert e["slug"] in KNOWN_MODEL_SLUGS, e
        assert e.get("goal") in GOALS, e  # explicit on every entry, for consistency
        assert set(e.get("sources", ["kijiji"])) <= {
            "kijiji",
            "autotrader",
            "facebook",
            "sturgess",
            "cycleworld",
            "dewildt",
            "delmastro",
        }, e
