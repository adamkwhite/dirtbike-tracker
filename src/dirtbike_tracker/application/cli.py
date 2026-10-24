"""
CLI entry point for the dirt bike tracker.

Usage:
    python main.py scrape --model crf150f --region ontario
    python main.py scrape --model crf150f --source autotrader
    python main.py list --model crf150f
    python main.py stats --model crf150f
    python main.py report --model crf150f
    python main.py watch                      # scrape all models from config, notify on deals
"""

import platform
import subprocess  # nosec B404 - fixed argv, no shell
import webbrowser
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import click
import yaml

from ..domain.listing import Listing
from ..domain.scoring import MarketSnapshot, project_line, specs_label, vs_label
from ..infrastructure import drift as drift_check
from ..infrastructure import fb_scraper, notifier
from ..infrastructure.autotrader_scraper import scrape_model as scrape_autotrader
from ..infrastructure.backup import BackupError, backup_offsite
from ..infrastructure.dashboard import write_dashboard
from ..infrastructure.dashboard_server import make_server
from ..infrastructure.dealerspike import STURGESS
from ..infrastructure.escsite import CYCLE_WORLD, DEWILDT
from ..infrastructure.focus import DEL_MASTRO
from ..infrastructure.kijiji_scraper import fetch_ad_description as kijiji_ad_description
from ..infrastructure.kijiji_scraper import scrape_model as scrape_kijiji
from ..infrastructure.reporter import contact_label, render_report, write_report
from ..infrastructure.storage import SqliteStore, init_db
from .details import fetch_post_dates
from .markdowns import check_markdowns, markdown_rows
from .market import model_goal
from .ports import App, Notifier
from .watch import (
    check_source_health,
    model_snapshot,
    notified_ids,
    record_notified,
    sources_seen,
    watch_model,
)

ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = ROOT / "config" / "models.yml"
REPORTS_DIR = ROOT / "reports"
STATE_DIR = ROOT / "data" / "state"
DASHBOARD_PATH = ROOT / "dashboard.html"
# Hoss's watchlist (gitignored): its own alerts, and a tab on the main dashboard
FRIEND_CONFIG = ROOT / "config" / "friend.yml"
# Drift-check recordings (#28): third-party ads, gitignored, never committed
RECORDED_DIR = ROOT / "tests" / "recorded"

RATING_COLORS = {
    "DEAL": "green",
    "GOOD": "cyan",
    "FAIR": "yellow",
    "HIGH": "red",
    "UNKNOWN": "white",
}


def load_config(path: Path | None = None) -> dict:
    """Load models.yml (or another watchlist). Returns {} if the file doesn't exist."""
    path = path or CONFIG_PATH  # resolved per call, so tests can redirect CONFIG_PATH
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _autotrader_source(
    slug: str,
    region: str,
    pages: int,  # noqa: ARG001
    *,
    query: str | None = None,
    include_new: bool = False,
) -> list[Listing]:
    """AutoTrader as a ListingSource: one keyword page, province code instead of a region slug."""
    return scrape_autotrader(
        slug,
        "on" if region.lower() == "ontario" else region,
        query=query,
        include_new=include_new,
    )


def skip_unconfigured(send: Notifier) -> Notifier:
    """A Notifier that reports a missing NTFY_TOPIC and returns False instead of raising."""

    def wrapped(title: str, body: str, **kwargs: object) -> bool:
        try:
            return send(title, body, **kwargs)  # type: ignore[arg-type]
        except notifier.NotifyConfigError as exc:
            click.echo(f"  (notify skipped: {exc})")
            return False

    return wrapped  # type: ignore[return-value]


def build_app(config_path: Path | None = None) -> App:
    """The composition root: real adapters for every port (ports.py).

    Another watchlist (`--config config/friend.yml`) shares the DB but keeps its
    own alert state and dashboard, so its alerts go only to its own ntfy topic.
    """
    init_db()
    tabs: dict[str, dict] = {}
    if config_path is None:
        state_dir, dashboard_path = STATE_DIR, DASHBOARD_PATH
        tabs = {"Hoss": load_config(FRIEND_CONFIG)}
    else:
        state_dir = STATE_DIR / config_path.stem
        dashboard_path = ROOT / f"dashboard-{config_path.stem}.html"
    return App(
        store=SqliteStore(),
        sources={
            "kijiji": scrape_kijiji,
            "autotrader": _autotrader_source,
            # opt-in per model (models.yml sources); throttled to a few searches a day
            "facebook": fb_scraper.FacebookSource(state_dir / "fb_searches.json"),
            # a dealer's whole used inventory in one cached fetch (#99)
            "sturgess": STURGESS,
            "cycleworld": CYCLE_WORLD,  # same idea on the EquipmentSearch platform
            "dewildt": DEWILDT,
            "delmastro": DEL_MASTRO,  # Focus platform: a Typesense index, one query
        },
        # AutoTrader's search page already carries the description, so it has no fetcher
        detail_fetchers={"kijiji": kijiji_ad_description},
        notify=skip_unconfigured(notifier.notify),
        street_notify=partial(notifier.notify, topic=notifier.NTFY_STREET_TOPIC)
        if notifier.NTFY_STREET_TOPIC
        else None,
        config=load_config(config_path),
        state_dir=state_dir,
        dashboard_path=dashboard_path,
        reports_dir=REPORTS_DIR,
        ping=notifier.ping_healthcheck,
        # dealers' new stock, for the markdown watch (markdowns.py)
        new_stock={"delmastro": DEL_MASTRO.new_units},
        post_dates={"facebook": fb_scraper.ad_post_date},  # FB's search cards carry no date
        dashboard_tabs=tabs,
    )


def _snapshot(app: App, slug: str) -> MarketSnapshot:
    try:
        return model_snapshot(app, slug)
    except ValueError as exc:  # unknown goal in models.yml
        raise click.ClickException(str(exc)) from exc


def _write_dashboard(app: App, slugs: list[str], new_units: list[dict] | None = None) -> Path:
    """The dashboard: a Dirt tab (the project models) and a Street tab (the rest), then a tab
    per other watchlist (App.dashboard_tabs), each with the dealers' new units of its models
    (the last watch's, unless given). Empty tabs are left out."""
    snaps = [_snapshot(app, s) for s in slugs]
    tabs = {
        "Dirt": [s for s in snaps if model_goal(app.config, s.slug) == "project"],
        "Street": [s for s in snaps if model_goal(app.config, s.slug) != "project"],
    }
    for name, config in app.dashboard_tabs.items():
        other = replace(app, config=config)  # its goals, not models.yml's
        tabs[name] = [_snapshot(other, e["slug"]) for g in config.values() for e in g or []]
    units = markdown_rows(app) if new_units is None else new_units
    return write_dashboard({n: t for n, t in tabs.items() if t}, app.dashboard_path, units)


def _open_in_browser(path: Path) -> None:
    """Open a local file in the default browser, including from WSL.

    Under WSL, webbrowser.open finds no Windows browser and silently does
    nothing; hand the file to Windows via its \\wsl.localhost path instead.
    """
    if "microsoft" in platform.uname().release.lower():
        win_path = subprocess.run(
            ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(["explorer.exe", win_path], check=False)  # exits 1 even on success
    else:
        webbrowser.open(f"file://{path}")


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Another watchlist, e.g. config/friend.yml (default config/models.yml)",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None):
    """Dirt bike price tracker."""
    if ctx.obj is None:  # tests pass their own App
        ctx.obj = build_app(config_path and config_path.resolve())


@cli.command()
@click.option("--model", required=True, help="Model slug, e.g. crf150f")
@click.option("--region", default="ontario", help="Region slug")
@click.option(
    "--source",
    type=click.Choice(
        [
            "kijiji",
            "autotrader",
            "facebook",
            "sturgess",
            "cycleworld",
            "dewildt",
            "delmastro",
            "all",
        ]
    ),
    default="kijiji",
    help="all = kijiji + autotrader",
)
@click.option("--pages", default=3, help="Max pages to scrape (Kijiji only)")
@click.pass_obj
def scrape(app: App, model: str, region: str, source: str, pages: int):
    """Scrape a source for a model, save to DB."""
    all_listings = []
    scraped_ok = []
    sources = [source] if source != "all" else ["kijiji", "autotrader"]
    for src in sources:
        click.echo(f"Scraping {src} for {model} in {region}...")
        listings = app.sources[src](model, region, pages)
        all_listings.extend(listings)
        if listings:
            scraped_ok.append(src)
        click.echo(f"  {src}: {len(listings)} listings")

    stats = app.store.upsert_listings(all_listings)
    stale = app.store.mark_stale(scraped_ok, [model])
    click.echo(
        f"\nTotal {len(all_listings)}. "
        f"New: {stats['new']}, Updated: {stats['updated']}, "
        f"Price changed: {stats['price_changed']}, Cross-posts: {stats['cross_posts']}, "
        f"Marked stale: {stale}"
    )


@cli.command("list")
@click.option("--model", required=True)
@click.pass_obj
def list_cmd(app: App, model: str):
    """List active listings for a model with deal ratings."""
    scored = _snapshot(app, model).listings
    if not scored:
        click.echo(f"No active listings for {model}.")
        return
    click.echo(f"\n{len(scored)} active listings for {model}:\n")
    for listing in scored:
        rating = listing["rating"]
        delta = listing["delta_pct"]
        delta_str = f"{delta:+.0f}%" if delta is not None else "   "
        if listing["price"]:
            price_str = f"${int(listing['price']):>5}"
        else:
            state = listing.get("price_state", "unknown")
            label = {"please_contact": "CONTACT", "swap_trade": " TRADE", "unknown": "    ?"}
            price_str = label.get(state, "    ?")
        loc = (listing["location"] or "")[:22]
        title = (listing["title"] or "")[:40]
        source = listing.get("source", "?")[:4]
        click.echo(
            f"  [{source}] "
            f"{click.style(rating.ljust(7), fg=RATING_COLORS.get(rating, 'white'))} "
            f"{delta_str.rjust(5)} {vs_label(listing)}  {price_str}  fit {listing['fit']:>2}  "
            f"{loc:<22}  {title}"
        )
        click.echo(f"           {listing['url']}")
        if specs := specs_label(listing):
            click.echo(f"           {specs}")
        if contacted := contact_label(listing):
            click.echo(f"           {contacted}")


@cli.command()
@click.option("--model", required=True)
@click.pass_obj
def stats(app: App, model: str):
    """Market stats for a model."""
    snap = _snapshot(app, model)
    s, t = snap.stats, snap.trend
    click.echo(f"\nMarket stats for {model} (last 90 days):")
    if s["count"] == 0:
        click.echo("  No data yet.")
        return
    click.echo(f"  Listings:  {s['count']}")
    click.echo(f"  Low:       ${int(s['low'])}")
    click.echo(
        f"  Median:    ${int(s['median'])}"
        + (" (private sellers)" if s.get("sellers") == "private" else "")
    )
    if line := project_line(s):
        click.echo(f"  {line}")
    click.echo(f"  High:      ${int(s['high'])}")
    if t is not None:
        arrow = "▼" if t < 0 else "▲"
        click.echo(f"  Trend:     {arrow} {abs(t)}% (30d vs 30-90d)")


@cli.command()
@click.option("--model", required=True)
@click.option("--write", is_flag=True)
@click.pass_obj
def report(app: App, model: str, write: bool):
    """Generate a markdown market report."""
    if write:
        path = write_report(_snapshot(app, model), out_dir=app.reports_dir)
        click.echo(f"Report written to {path}")
    else:
        click.echo(render_report(_snapshot(app, model)))


@cli.command()
@click.option("--region", default="ontario")
@click.option("--notify/--no-notify", default=True)
@click.option("--group", default=None, help="Only this group from models.yml")
@click.pass_obj
def watch(app: App, region: str, notify: bool, group: str):
    """
    Scrape all configured models, score, notify on new deals.
    Designed to run on a cron schedule.
    """
    config = app.config
    if not config:
        click.echo("No models configured. Populate config/models.yml (or the --config file).")
        return

    groups = [group] if group else list(config.keys())
    all_targets = [{**entry, "group": g} for g in groups for entry in config.get(g) or []]

    click.echo(f"Watching {len(all_targets)} models from groups: {', '.join(groups)}")
    already_notified = notified_ids(app)
    source_counts: Counter = Counter()
    newly_notified = set()
    totals = {
        "new": 0, "updated": 0, "deals": 0, "goods": 0, "target_hits": 0, "drops": 0, "matches": 0,
    }  # fmt: skip

    for target in all_targets:
        notified, skipped = watch_model(
            app, target, region, notify, already_notified, source_counts, totals
        )
        newly_notified |= notified
        for line in skipped:
            click.echo(f"  {line}")

    if dated := fetch_post_dates(app.post_dates, app.store):
        click.echo(f"Post dates read: {dated}")
    seen = sources_seen(app, source_counts)
    app.store.mark_stale([s for s, n in seen.items() if n], [t["slug"] for t in all_targets])
    record_notified(app, already_notified | newly_notified)
    if not group:  # a subset of models coming back empty doesn't mean a scraper broke
        for warning in check_source_health(app, seen, notify):
            click.echo(f"  WARNING: {warning}")
    new_units, lines = check_markdowns(app, notify)
    for line in lines:
        click.echo(f"  {line}")

    # Refresh the dashboard with every watch run
    all_slugs = [t["slug"] for t in all_targets]
    try:
        _write_dashboard(app, all_slugs, new_units)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"  (dashboard write failed: {exc})")

    click.echo(
        f"\nWatch complete. New: {totals['new']}, Updated: {totals['updated']}, "
        f"DEAL alerts: {totals['deals']}, GOOD alerts: {totals['goods']}, "
        f"Target hits: {totals['target_hits']}, Price drops: {totals['drops']}"
        + (f", Matches: {totals['matches']}" if totals["matches"] else "")
    )
    click.echo(f"Dashboard: file://{app.dashboard_path}")
    app.ping()


@cli.command()
@click.option("--models", help="Comma-separated model slugs. Defaults to all from models.yml")
@click.option("--open", "open_browser", is_flag=True, help="Open in browser after writing")
@click.pass_obj
def dashboard(app: App, models: str, open_browser: bool):
    """Write the HTML dashboard to dashboard.html at the project root."""
    slugs = [s.strip() for s in models.split(",")] if models else _config_slugs(app)
    if not slugs:
        click.echo("No models configured. Pass --models or populate config/models.yml")
        return

    path = _write_dashboard(app, slugs)
    click.echo(f"Dashboard written: file://{path}")
    if open_browser:
        _open_in_browser(path)


def _config_slugs(app: App) -> list[str]:
    return [entry["slug"] for entries in app.config.values() for entry in entries or []]


def hide_listing(app: App, listing_id: int, hidden: bool, note: str | None) -> bool:
    """Hide (or unhide) a listing and rebuild the dashboard. False if there's no such listing."""
    if not app.store.set_hidden(listing_id, hidden, note):
        return False
    _write_dashboard(app, _config_slugs(app))
    return True


def note_listing(app: App, listing_id: int, note: str | None) -> bool:
    """Save a listing's note and rebuild the dashboard. False if there's no such listing."""
    if not app.store.set_note(listing_id, note):
        return False
    _write_dashboard(app, _config_slugs(app))
    return True


def year_listing(app: App, listing_id: int, year: int | None) -> bool:
    """Save a listing's year and rebuild the dashboard. False if there's no such listing."""
    if not app.store.set_year(listing_id, year):
        return False
    _write_dashboard(app, _config_slugs(app))
    return True


@cli.command()
@click.option("--port", default=8765, help="Localhost port (tailscale serve proxies it)")
@click.pass_obj
def serve(app: App, port: int):
    """Serve the dashboard on localhost, with its hide/unhide buttons working."""
    server = make_server(
        app.dashboard_path,
        partial(hide_listing, app),
        partial(note_listing, app),
        partial(year_listing, app),
        port=port,
    )
    click.echo(f"Serving {app.dashboard_path.name} on http://127.0.0.1:{port}")
    server.serve_forever()


@cli.command()
@click.option("--slot", type=click.Choice(["nightly", "weekly"]), default="nightly")
@click.pass_obj
def backup(app: App, slot: str):
    """Upload a DB + state snapshot to Google Drive (rclone `jobbackup` remote)."""
    try:
        rows, size = backup_offsite(slot, state_dir=app.state_dir)
    except BackupError as exc:
        click.echo(f"Backup FAILED ({slot}): {exc}")
        app.notify(
            title=f"dirtbike backup FAILED ({slot})", body=str(exc), priority=4, tags=["warning"]
        )
        raise SystemExit(1) from exc
    click.echo(f"Backup OK ({slot}): {rows} listings, {size} bytes")


@cli.command()
@click.option(
    "--dir",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where recordings go (default tests/recorded/, gitignored)",
)
@click.option("--replay", is_flag=True, help="Re-check the existing recordings, don't re-record")
@click.option("--notify/--no-notify", default=True, help="Push an ntfy alert on problems")
@click.pass_obj
def drift(app: App, out_dir: Path | None, replay: bool, notify: bool):
    """Site-drift check: record live Kijiji/AutoTrader pages and the Sturgess inventory file, verify our parsers still read them."""
    out = out_dir or RECORDED_DIR
    found = drift_check.run(out, replay=replay)
    if not found:
        click.echo(f"Drift check OK ({out})")
        return
    click.echo(f"Drift check FAILED: {len(found)} problem(s)")
    for problem in found:
        click.echo(f"  - {problem}")
    if notify:
        app.notify(
            title=f"Site drift: {len(found)} parser problem(s)",
            body="\n".join(found[:10])
            + '\nRe-check: dirtbike drift --replay. See README "Site drift".',
            priority=4,
            tags=["warning"],
        )
    raise SystemExit(1)


@cli.command("fb-login")
def fb_login():
    """Log in to Facebook once in a visible browser; the session is kept for the facebook source."""
    click.echo(
        f"Opening a browser (profile: {fb_scraper.PROFILE_DIR}). Log in, then close the window."
    )
    try:
        fb_scraper.login()
    except ImportError as exc:
        raise click.ClickException("Playwright not installed: pip install -e '.[fb]'") from exc
    click.echo("Saved. watch will search Facebook with this session.")


@cli.command()
@click.argument("ref")
@click.pass_obj
def dismiss(app: App, ref: str):
    """Hide a listing (Kijiji/AutoTrader URL or external id) from alerts and the dashboard."""
    _set_dismissed(app, ref, dismissed=True)


@cli.command()
@click.argument("ref")
@click.pass_obj
def undismiss(app: App, ref: str):
    """Reverse `dismiss`."""
    _set_dismissed(app, ref, dismissed=False)


def _set_dismissed(app: App, ref: str, dismissed: bool) -> None:
    titles = app.store.set_dismissed(ref, dismissed)
    if not titles:
        click.echo(f"No listing matches {ref!r}")
        raise SystemExit(1)
    verb = "Dismissed" if dismissed else "Restored"
    for title in titles:
        click.echo(f"{verb}: {title}")


@cli.command()
@click.argument("ref")
@click.option("--note", help='What was said, e.g. "offered 750, waiting"')
@click.option(
    "--on", "on_date", type=click.DateTime(["%Y-%m-%d"]), help="Contact date (default now)"
)
@click.option("--clear", is_flag=True, help="Forget the contact")
@click.pass_obj
def contact(app: App, ref: str, note: str | None, on_date: datetime | None, clear: bool):
    """Log messaging a listing's seller (URL or external id); shown on the dashboard."""
    at = None if clear else on_date or datetime.now(UTC).replace(tzinfo=None)
    titles = app.store.set_contact(ref, at, note)
    if not titles:
        click.echo(f"No listing matches {ref!r}")
        raise SystemExit(1)
    for title in titles:
        click.echo(f"{'Cleared contact' if clear else 'Contacted'}: {title}")


@cli.command()
@click.pass_obj
def models(app: App):
    """Show configured models from models.yml."""
    config = app.config
    if not config:
        click.echo(f"No config at {CONFIG_PATH}")
        return
    for group, entries in config.items():
        click.echo(f"\n[{group}]")
        for e in entries or []:
            target = f" (target ${e.get('target_price', '?')})" if e.get("target_price") else ""
            sources = ",".join(e.get("sources", ["kijiji"]))
            click.echo(f"  {e['slug']:<10}  sources={sources}{target}")
            if e.get("notes"):
                click.echo(f"             {e['notes']}")


if __name__ == "__main__":
    cli()
