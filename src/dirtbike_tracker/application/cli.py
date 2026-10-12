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

import contextlib
from pathlib import Path

import click
import yaml

from ..infrastructure.autotrader_scraper import scrape_model as scrape_autotrader
from ..infrastructure.dashboard import write_dashboard
from ..infrastructure.kijiji_scraper import scrape_model as scrape_kijiji
from ..infrastructure.notifier import NotifyConfigError, notify_deal, notify_good
from ..infrastructure.reporter import render_report, write_report
from ..infrastructure.storage import init_db, mark_stale, upsert_listings
from .market import snapshot

ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = ROOT / "config" / "models.yml"
REPORTS_DIR = ROOT / "reports"
STATE_DIR = ROOT / "data" / "state"
DASHBOARD_PATH = ROOT / "dashboard.html"

RATING_COLORS = {
    "DEAL": "green",
    "GOOD": "cyan",
    "FAIR": "yellow",
    "HIGH": "red",
    "UNKNOWN": "white",
}


def load_config() -> dict:
    """Load models.yml. Returns {} if the file doesn't exist."""
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def _scrape_one(model: str, region: str, source: str, pages: int) -> list:
    """Scrape one source for one model and return the raw listings."""
    if source == "kijiji":
        return scrape_kijiji(model, region, pages)
    if source == "autotrader":
        region_code = "on" if region.lower() == "ontario" else region
        return scrape_autotrader(model, region_code)
    return []


def _notified_ids() -> set:
    """Load the set of listing external_ids we've already notified on."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "notified_ids.txt"
    if not path.exists():
        return set()
    return set(path.read_text().splitlines())


def _record_notified(ids: set) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "notified_ids.txt"
    path.write_text("\n".join(sorted(ids)))


@click.group()
def cli():
    """Dirt bike price tracker."""
    init_db()


@cli.command()
@click.option("--model", required=True, help="Model slug, e.g. crf150f")
@click.option("--region", default="ontario", help="Region slug")
@click.option("--source", type=click.Choice(["kijiji", "autotrader", "all"]), default="kijiji")
@click.option("--pages", default=3, help="Max pages to scrape (Kijiji only)")
def scrape(model: str, region: str, source: str, pages: int):
    """Scrape a source for a model, save to DB."""
    all_listings = []
    sources = [source] if source != "all" else ["kijiji", "autotrader"]
    for src in sources:
        click.echo(f"Scraping {src} for {model} in {region}...")
        listings = _scrape_one(model, region, src, pages)
        all_listings.extend(listings)
        click.echo(f"  {src}: {len(listings)} listings")

    stats = upsert_listings(all_listings)
    stale = mark_stale()
    click.echo(
        f"\nTotal {len(all_listings)}. "
        f"New: {stats['new']}, Updated: {stats['updated']}, "
        f"Price changed: {stats['price_changed']}, Cross-posts: {stats['cross_posts']}, "
        f"Marked stale: {stale}"
    )


@cli.command("list")
@click.option("--model", required=True)
def list_cmd(model: str):
    """List active listings for a model with deal ratings."""
    scored = snapshot(model).listings
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
            f"{delta_str.rjust(5)} vs median  {price_str}  {loc:<22}  {title}"
        )
        click.echo(f"           {listing['url']}")


@cli.command()
@click.option("--model", required=True)
def stats(model: str):
    """Market stats for a model."""
    snap = snapshot(model)
    s, t = snap.stats, snap.trend
    click.echo(f"\nMarket stats for {model} (last 90 days):")
    if s["count"] == 0:
        click.echo("  No data yet.")
        return
    click.echo(f"  Listings:  {s['count']}")
    click.echo(f"  Low:       ${int(s['low'])}")
    click.echo(f"  Median:    ${int(s['median'])}")
    click.echo(f"  High:      ${int(s['high'])}")
    if t is not None:
        arrow = "▼" if t < 0 else "▲"
        click.echo(f"  Trend:     {arrow} {abs(t)}% (30d vs 30-90d)")


@cli.command()
@click.option("--model", required=True)
@click.option("--write", is_flag=True)
def report(model: str, write: bool):
    """Generate a markdown market report."""
    if write:
        path = write_report(snapshot(model), out_dir=REPORTS_DIR)
        click.echo(f"Report written to {path}")
    else:
        click.echo(render_report(snapshot(model)))


@cli.command()
@click.option("--region", default="ontario")
@click.option("--notify/--no-notify", default=True)
@click.option("--group", default=None, help="Only this group from models.yml")
def watch(region: str, notify: bool, group: str):
    """
    Scrape all configured models, score, notify on new deals.
    Designed to run on a cron schedule.
    """
    config = load_config()
    if not config:
        click.echo(f"No config at {CONFIG_PATH}. Create one from the example.")
        return

    groups = [group] if group else list(config.keys())
    all_targets = []
    for g in groups:
        for entry in config.get(g, []) or []:
            all_targets.append({**entry, "group": g})

    click.echo(f"Watching {len(all_targets)} models from groups: {', '.join(groups)}")
    already_notified = _notified_ids()
    newly_notified = set()
    totals = {"new": 0, "updated": 0, "deals": 0, "goods": 0, "target_hits": 0}

    for target in all_targets:
        slug = target["slug"]
        for source in target.get("sources", ["kijiji"]):
            listings = _scrape_one(slug, region, source, pages=3)
            stats = upsert_listings(listings)
            totals["new"] += stats["new"]
            totals["updated"] += stats["updated"]

        # Re-score after upsert
        scored = snapshot(slug).listings
        target_price = target.get("target_price")

        for listing in scored:
            lid = f"{listing['source']}:{listing['external_id']}"
            if lid in already_notified:
                continue

            is_deal = listing["rating"] == "DEAL"
            is_good = listing["rating"] == "GOOD"
            is_target_hit = (
                target_price is not None
                and listing.get("price") is not None
                and listing["price"] <= target_price
            )

            if is_deal or is_target_hit:
                listing_for_notify = {**listing, "model_slug": slug}
                if notify:
                    try:
                        notify_deal(listing_for_notify)
                    except NotifyConfigError as exc:
                        click.echo(f"  (notify skipped: {exc})")
                totals["deals"] += int(is_deal)
                totals["target_hits"] += int(is_target_hit)
                newly_notified.add(lid)
            elif is_good:
                listing_for_notify = {**listing, "model_slug": slug}
                if notify:
                    with contextlib.suppress(NotifyConfigError):
                        notify_good(listing_for_notify)
                totals["goods"] += 1
                newly_notified.add(lid)

    mark_stale()
    _record_notified(already_notified | newly_notified)

    # Refresh the dashboard with every watch run
    all_slugs = [t["slug"] for t in all_targets]
    try:
        write_dashboard([snapshot(s) for s in all_slugs], DASHBOARD_PATH)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"  (dashboard write failed: {exc})")

    click.echo(
        f"\nWatch complete. New: {totals['new']}, Updated: {totals['updated']}, "
        f"DEAL alerts: {totals['deals']}, GOOD alerts: {totals['goods']}, "
        f"Target hits: {totals['target_hits']}"
    )
    click.echo(f"Dashboard: file://{DASHBOARD_PATH}")


@cli.command()
@click.option("--models", help="Comma-separated model slugs. Defaults to all from models.yml")
@click.option("--open", "open_browser", is_flag=True, help="Open in browser after writing")
def dashboard(models: str, open_browser: bool):
    """Write the HTML dashboard to dashboard.html at the project root."""
    if models:
        slugs = [s.strip() for s in models.split(",")]
    else:
        config = load_config()
        slugs = []
        for group_entries in config.values():
            for entry in group_entries or []:
                slugs.append(entry["slug"])
        if not slugs:
            click.echo("No models configured. Pass --models or populate config/models.yml")
            return

    path = write_dashboard([snapshot(s) for s in slugs], DASHBOARD_PATH)
    click.echo(f"Dashboard written: file://{path}")
    if open_browser:
        import webbrowser

        webbrowser.open(f"file://{path}")


@cli.command()
def models():
    """Show configured models from models.yml."""
    config = load_config()
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
