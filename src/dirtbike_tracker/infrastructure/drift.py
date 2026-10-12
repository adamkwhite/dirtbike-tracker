"""Site-drift check (#28): record live pages, run our parsers over them, list what broke.

Used by `dirtbike drift` (daily cron, alerts on problems) and tests/drift (local).
Recordings are third-party ads with private sellers' details: they go in a
gitignored directory and must never be committed (the repo is public).
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..domain.listing import Listing
from . import autotrader_scraper, kijiji_scraper

# A kid dirt bike (cards, snippets) and a street bike (year/km form fields, dealer stock)
KIJIJI_SLUGS = ("crf150f", "rebel500")
AUTOTRADER_SLUG = "rebel500"  # its results include new dealer stock (offerType N)
PRICE_STATES = {"numeric", "please_contact", "swap_trade", "unknown"}
AD_PAGE = "kijiji_ad.html"
AUTOTRADER_PAGE = "autotrader_search.html"


def record(out: Path) -> None:
    """Fetch the pages and write them, plus manifest.json, to `out`. Raises on a failed fetch."""
    out.mkdir(parents=True, exist_ok=True)
    pages: dict[str, dict] = {}
    for slug in KIJIJI_SLUGS:
        url = kijiji_scraper.build_search_url(slug)
        name = f"kijiji_search_{slug}.html"
        (out / name).write_text(kijiji_scraper.fetch_page(url))
        pages[name] = {"url": url, "slug": slug}

    # one ad page, for the full description (#71): the first listing on the first search
    first_search = (out / f"kijiji_search_{KIJIJI_SLUGS[0]}.html").read_text()
    if first := kijiji_scraper.parse_listings(first_search, KIJIJI_SLUGS[0]):
        (out / AD_PAGE).write_text(kijiji_scraper.fetch_page(first[0].url))
        pages[AD_PAGE] = {"url": first[0].url, "external_id": first[0].external_id}

    url = autotrader_scraper.build_search_url(AUTOTRADER_SLUG)
    (out / AUTOTRADER_PAGE).write_text(autotrader_scraper.fetch_page(url))
    pages[AUTOTRADER_PAGE] = {"url": url, "slug": AUTOTRADER_SLUG}

    manifest = {"recorded_at": datetime.now(UTC).isoformat(timespec="seconds"), "pages": pages}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


def _field_problems(name: str, listings: list[Listing], url_prefix: str) -> list[str]:
    if not listings:
        return [f"{name}: parsed no listings"]
    found = []
    for x in listings:
        if not (x.external_id and x.title and x.url.startswith(url_prefix)):
            found.append(f"{name}: listing missing id/title/url: {x.url!r}")
        if x.price_state not in PRICE_STATES or (x.price is None and x.price_state == "numeric"):
            found.append(f"{name}: bad price {x.price!r} / {x.price_state!r}")
    if not any(x.price for x in listings):
        found.append(f"{name}: no listing has a price")
    if not any(x.image_url for x in listings):
        found.append(f"{name}: no photos (#10)")
    return found


def _kijiji_search_problems(name: str, html: str, slug: str) -> list[str]:
    listings = kijiji_scraper.parse_listings(html, slug)
    found = _field_problems(name, listings, "https://www.kijiji.ca/v-")
    if any(not re.fullmatch(r"\d{10,}", x.external_id) for x in listings):
        found.append(f"{name}: ad ids are no longer 10+ digit numbers")
    if listings and not any(x.description for x in listings):
        found.append(f"{name}: no search-page snippets (#76)")
    if listings and not any(x.posted_text for x in listings):
        found.append(f"{name}: no activationDate")
    meta = kijiji_scraper._ad_meta(BeautifulSoup(html, "html.parser"))
    new_ids = {ad_id for ad_id, facts in meta.items() if facts.get("new")}
    if leaked := {x.external_id for x in listings} & new_ids:
        found.append(f"{name}: new dealer stock not skipped: {sorted(leaked)}")
    if slug == "rebel500" and listings:  # street bikes fill in the form fields
        if not any(x.year for x in listings):
            found.append(f"{name}: caryear stopped parsing")
        if not any(x.km for x in listings):
            found.append(f"{name}: carmileageinkms stopped parsing")
    return found


def _autotrader_problems(name: str, html: str, slug: str) -> list[str]:
    script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    try:
        raw = json.loads(script.get_text())["props"]["pageProps"]["listings"]  # type: ignore[union-attr]
    except (AttributeError, ValueError, KeyError, TypeError):
        return [f"{name}: __NEXT_DATA__ listings not found"]
    new_ids = {str(ad["id"]) for ad in raw if (ad.get("vehicle") or {}).get("offerType") == "N"}
    listings = autotrader_scraper.parse_listings(html, slug)
    found = _field_problems(name, listings, "https://www.autotrader.ca/")
    if leaked := {x.external_id for x in listings} & new_ids:
        found.append(f"{name}: new dealer stock not skipped: {sorted(leaked)}")
    if listings and not any(x.year for x in listings):
        found.append(f"{name}: modelYear stopped parsing")
    if listings and not any(x.km for x in listings):
        found.append(f"{name}: mileageInKm stopped parsing")
    return found


def problems(out: Path) -> list[str]:
    """Everything our parsers no longer read from the recordings in `out`. [] = no drift."""
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        return [f"no recordings in {out}"]
    pages = json.loads(manifest_path.read_text())["pages"]
    found = []
    for name, info in sorted(pages.items()):
        html = (out / name).read_text()
        if name.startswith("kijiji_search_"):
            found += _kijiji_search_problems(name, html, info["slug"])
        elif name == AD_PAGE:
            if not kijiji_scraper.parse_ad_description(html, info["external_id"]):
                found.append(f"{name}: __APOLLO_STATE__ description stopped parsing (#71)")
        elif name == AUTOTRADER_PAGE:
            found += _autotrader_problems(name, html, info["slug"])
    return found


def run(out: Path, replay: bool = False) -> list[str]:
    """Record (unless `replay`) and check. A failed fetch is reported, not raised."""
    if not replay:
        try:
            record(out)
        except requests.RequestException as exc:
            return [f"recording failed: {exc}"]
    return problems(out)
