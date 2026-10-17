"""New-bike markdown watch: dealers' new stock of the tracked models, alerting on a price cut.

New units stay out of the listings table: MSRP isn't the used market, and against a used
median every new bike rates HIGH, so a markdown would never alert there. Instead each
watch run asks every `app.new_stock` dealer for its new units of the tracked models and
compares prices with the last run (`new_stock.json` in the state dir). It alerts when a
unit's price drops, or when a unit first seen is already below the dealer's regular price
or MSRP. The first run only records, like a --no-notify seed.
"""

import json
from dataclasses import asdict

from ..domain.listing import NewUnit
from .ports import App, Notifier
from .watch import notifier_for

STATE_FILE = "new_stock.json"


def _load(app: App) -> dict | None:
    path = app.state_dir / STATE_FILE
    return json.loads(path.read_text()) if path.exists() else None


def markdown_rows(app: App) -> list[dict]:
    """The new units the last watch saw, for the dashboard."""
    return list((_load(app) or {}).get("units", []))


def _reason(unit: NewUnit, before: float | None) -> tuple[str, float] | None:
    """(why, the price it beat) when this unit is worth an alert."""
    if before is not None:
        return ("Price cut", before) if unit.price < before else None
    if unit.was and unit.price < unit.was:
        return "On sale", unit.was
    return None


def _send(send: Notifier, unit: NewUnit, why: str, was: float) -> bool:
    save = was - unit.price
    return send(
        title=f"MARKDOWN: {unit.model_slug.upper()} ${unit.price:,.0f} (save ${save:,.0f})",
        body=f"{why}, was ${was:,.0f} ({save / was:.0%} off).\n{unit.title}\n{unit.dealer}\n{unit.url}",
        priority=3,
        tags=["label"],
        click_url=unit.url,
        attach_url=unit.image_url,
    )


def _price_to_record(
    send: Notifier,
    unit: NewUnit,
    before: float | None,
    seeded: bool,
    notify: bool,
    lines: list[str],
) -> float | None:
    """Alert on this unit if it's worth it; the price to save for next run (None: none yet).
    A first run (not `seeded`) only records; an unsent push keeps the old price, so the next
    run alerts again; a quiet run lists the markdown instead of sending it."""
    reason = _reason(unit, before) if seeded else None
    if not reason:
        return unit.price
    if not notify:
        lines.append(f"quiet markdown: {unit.model_slug} ${unit.price:.0f} {unit.title} {unit.url}")
        return unit.price
    return unit.price if _send(send, unit, *reason) else before


def check_markdowns(app: App, notify: bool) -> tuple[list[dict], list[str]]:
    """Fetch every dealer's new stock, alert on price cuts, save prices.

    Returns the units seen (for the dashboard) and lines for cli to print: a dealer that
    failed, and on a quiet (--no-notify) run the markdowns it didn't send."""
    groups = {e["slug"]: g for g, entries in app.config.items() for e in entries or []}
    slugs = list(groups)
    state = _load(app)
    old_prices: dict[str, float] = (state or {}).get("prices", {})
    prices: dict[str, float] = {}
    units: list[dict] = []
    lines: list[str] = []
    for dealer, fetch in app.new_stock.items():
        try:
            found = fetch(slugs)
        # a dealer site that's down skips this run; its last prices and units stay
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{dealer}: new stock fetch failed: {exc}")
            prices |= {k: v for k, v in old_prices.items() if k.startswith(f"{dealer}:")}
            units += [u for u in markdown_rows(app) if u["dealer_key"] == dealer]
            continue
        for unit in found:
            key = f"{dealer}:{unit.unit_id}"
            units.append(asdict(unit) | {"dealer_key": dealer})
            send = notifier_for(app, groups.get(unit.model_slug))
            price = _price_to_record(
                send, unit, old_prices.get(key), state is not None, notify, lines
            )
            if price is not None:
                prices[key] = price
    app.state_dir.mkdir(parents=True, exist_ok=True)
    (app.state_dir / STATE_FILE).write_text(
        json.dumps({"prices": prices, "units": units}, indent=1)
    )
    return units, lines
