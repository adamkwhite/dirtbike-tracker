"""`dirtbike watch` orchestration: scrape -> store -> score -> read full ads -> re-score -> alert.

Runs against an App (ports.py), so tests pass fakes rather than patching modules.
"""

import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..domain.scoring import FIT_ALERT_MIN, FIT_NEUTRAL, MarketSnapshot, matches_spec, under_target
from .alerts import notify_deal, notify_good, notify_match
from .details import fetch_details
from .market import model_goal, snapshot
from .ports import App, Notifier

Alert = Callable[[Notifier, dict], bool]

STALE_SOURCE_AFTER = timedelta(hours=24)


def model_snapshot(app: App, slug: str) -> MarketSnapshot:
    """snapshot() scored for the model's goal in models.yml."""
    return snapshot(slug, app.store, model_goal(app.config, slug))


def alert_for(
    listing: dict, target_price: float | None, every: bool = False
) -> tuple[Alert, str, str] | None:
    """(alert fn, dedupe key, totals bucket) for a listing this run, or None.

    Price drops are keyed by the new price, so a listing already alerted on
    alerts again each time it drops into GOOD/DEAL territory. `every` (models.yml
    `alert: all`) alerts on each new listing and each drop, whatever its rating.
    """
    lid = f"{listing['source']}:{listing['external_id']}"
    if every:
        if listing.get("price_drop_from") is not None:
            return notify_match, f"drop:{lid}:{listing['price']:.0f}", "drops"
        return notify_match, lid, "matches"
    # red flags (no ownership, parts bike...) make a cheap price irrelevant
    if listing.get("fit", FIT_NEUTRAL) < FIT_ALERT_MIN:
        return None
    rating = listing["rating"]
    if listing.get("price_drop_from") is not None and rating in ("DEAL", "GOOD"):
        alert = notify_deal if rating == "DEAL" else notify_good
        return alert, f"drop:{lid}:{listing['price']:.0f}", "drops"
    if rating == "DEAL":
        return notify_deal, lid, "deals"
    if under_target(listing, target_price):
        return notify_deal, lid, "target_hits"
    if rating == "GOOD":
        return notify_good, lid, "goods"
    return None


def watch_model(
    app: App,
    target: dict,
    region: str,
    notify: bool,
    already_notified: set,
    source_counts: Counter,
    totals: dict,
) -> set:
    """Scrape, upsert and alert for one configured model. Returns the newly notified keys."""
    slug = target["slug"]
    # only what the entry sets, so sources (and test fakes) without these options still work
    opts = {k: target[k] for k in ("query", "include_new") if k in target}
    for source in target.get("sources", ["kijiji"]):
        scrape = app.sources.get(source)
        listings = scrape(slug, region, 3, **opts) if scrape else []
        listings = [
            x
            for x in listings
            if matches_spec(
                x, target.get("min_year"), target.get("require", ()), target.get("max_year")
            )
        ]
        stats = app.store.upsert_listings(listings)
        totals["new"] += stats["new"]
        totals["updated"] += stats["updated"]
        source_counts[source] += len(listings)

    # Re-score after upsert. Read the full ad of anything that could alert first,
    # so a red flag in the body stops the alert before its dedupe key is spent.
    scored = model_snapshot(app, slug).listings
    if fetch_details(scored, target.get("target_price"), app.detail_fetchers, app.store):
        scored = model_snapshot(app, slug).listings
    newly_notified = set()
    for listing in scored:
        choice = alert_for(listing, target.get("target_price"), target.get("alert") == "all")
        if choice is None or choice[1] in already_notified:
            continue
        alert, key, bucket = choice
        # --no-notify still records, so re-enabling doesn't replay old alerts;
        # a send that fails stays unrecorded and retries next run
        if not notify or alert(app.notify, {**listing, "model_slug": slug}):
            newly_notified.add(key)
        totals[bucket] += 1
    return newly_notified


def sources_seen(app: App, counts: Counter) -> Counter:
    """Listings per source, with a whole-inventory source (a dealer's stock file) that fetched
    fine counted as seen even with no matches: its unmatched listings are sold, not missed."""
    return Counter(
        {
            s: n or int(getattr(app.sources.get(s), "last_fetch_ok", False))
            for s, n in counts.items()
        }
    )


def notified_ids(app: App) -> set:
    """Dedupe keys of alerts already sent."""
    path = app.state_dir / "notified_ids.txt"
    return set(path.read_text().splitlines()) if path.exists() else set()


def record_notified(app: App, ids: set) -> None:
    app.state_dir.mkdir(parents=True, exist_ok=True)
    (app.state_dir / "notified_ids.txt").write_text("\n".join(sorted(ids)))


def stale_sources(health: dict, counts: Counter, now: datetime) -> list[str]:
    """Update per-source health in place; return sources due a 'no results' alert.

    A source is stale when it has returned 0 listings across every model for
    STALE_SOURCE_AFTER. Alerts repeat at most once per STALE_SOURCE_AFTER.
    """
    due = []
    for source, n in counts.items():
        h = health.setdefault(source, {"last_ok": now.isoformat(), "last_alert": None})
        if n:
            h["last_ok"], h["last_alert"] = now.isoformat(), None
            continue
        quiet_for = now - datetime.fromisoformat(h["last_ok"])
        last_alert = h["last_alert"] and datetime.fromisoformat(h["last_alert"])
        if quiet_for >= STALE_SOURCE_AFTER and (
            not last_alert or now - last_alert >= STALE_SOURCE_AFTER
        ):
            h["last_alert"] = now.isoformat()
            due.append(source)
    return due


def check_source_health(app: App, counts: Counter, notify: bool) -> list[str]:
    """Persist source health and alert on scrapers that have gone quiet. Returns warnings."""
    path = app.state_dir / "source_health.json"
    health = json.loads(path.read_text()) if path.exists() else {}
    stale = stale_sources(health, counts, datetime.now(UTC))
    app.state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(health, indent=1))

    warnings = []
    for source in stale:
        since = health[source]["last_ok"][:16].replace("T", " ")
        warnings.append(f"{source} returned 0 listings for every model since {since} UTC")
        if notify:
            app.notify(
                title=f"{source} scraper returned nothing for 24h",
                body=(
                    f"0 listings across all models since {since} UTC. "
                    f"Likely the site's markup or search URL changed: "
                    f"check infrastructure/{source}_scraper.py."
                ),
                priority=4,
                tags=["warning"],
            )
    return warnings
