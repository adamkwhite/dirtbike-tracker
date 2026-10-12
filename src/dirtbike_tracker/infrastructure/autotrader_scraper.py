"""
AutoTrader.ca scraper (scaffolding + simple keyword path).

AutoTrader has thin inventory for used off-road dirt bikes compared to
Kijiji. It is most useful for:
  - Newer bikes (CRF250F, CRF450F, Sur-Ron, Stark)
  - Dealer-certified / plated bikes
  - Cross-referencing high-end asking prices

Weekend 2 ships the simple keyword path. Model-code category mapping
(e.g. ma31mo73607 = Honda CRF 125) is deferred to Weekend 3 when we have
a reason to filter precisely.
"""

import json
import random
import time
from datetime import UTC, datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .kijiji_scraper import (
    PRICE_STATE_NUMERIC,
    PRICE_STATE_UNKNOWN,
    USER_AGENTS,
    Listing,
    detect_models_in_title,
)

BASE_URL = "https://www.autotrader.ca"
RATE_LIMIT_SECONDS = 2.5


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),  # NOSONAR - UA rotation, not security
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
    }


def build_search_url(model_slug: str, region: str = "on") -> str:
    """Keyword-based AutoTrader search URL in a province."""
    return f"{BASE_URL}/motorcycles-atvs/lst/reg_{region}?loc=Ontario&kwd={quote(model_slug)}"


def fetch_page(url: str, timeout: int = 20) -> str:
    time.sleep(RATE_LIMIT_SECONDS + random.uniform(0, 0.8))
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return str(response.text)


def parse_listings(html: str, model_slug: str) -> list[Listing]:
    """Extract listings from the search page's __NEXT_DATA__ (props.pageProps.listings).

    AutoTrader is a Next.js app; the rendered cards have no stable selectors,
    but the embedded page data does. New stock (offerType "N") is skipped:
    it's dealer MSRP, not used-market prices.
    """
    script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    try:
        ads = json.loads(script.get_text())["props"]["pageProps"]["listings"] if script else []
    except (ValueError, KeyError, TypeError):
        return []

    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    parsed = (_ad_to_listing(ad, model_slug, now) for ad in ads)
    return [listing for listing in parsed if listing is not None]


def _ad_to_listing(ad: dict, model_slug: str, now: str) -> Listing | None:
    """One __NEXT_DATA__ ad -> Listing, or None for new stock / other models."""
    vehicle = ad.get("vehicle") or {}
    if vehicle.get("offerType") == "N":
        return None

    parts = [vehicle.get("modelYear"), vehicle.get("make"), vehicle.get("model")]
    parts.append(vehicle.get("modelVersionInput"))
    title = " ".join(str(p) for p in parts if p)
    detected = detect_models_in_title(title)
    if model_slug not in detected and model_slug.lower() not in title.lower():
        return None

    price = (ad.get("price") or {}).get("priceRaw")
    return Listing(
        source="autotrader",
        external_id=str(ad["id"]),
        url=ad["url"],
        title=title,
        price=float(price) if price else None,
        price_state=PRICE_STATE_NUMERIC if price else PRICE_STATE_UNKNOWN,
        location=(ad.get("location") or {}).get("city") or "",
        posted_text="",
        description=ad.get("description") or "",
        model_slugs=detected if detected else [model_slug],
        scraped_at=now,
    )


def scrape_model(model_slug: str, region: str = "on") -> list[Listing]:
    """Scrape AutoTrader keyword search for a model."""
    url = build_search_url(model_slug, region)
    try:
        html = fetch_page(url)
    except requests.RequestException as exc:
        print(f"AutoTrader fetch failed: {exc}")  # noqa: T201
        return []
    return parse_listings(html, model_slug)
