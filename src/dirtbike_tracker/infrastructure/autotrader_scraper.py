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

import random
import re
import time
from datetime import datetime
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
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
    }


def build_search_url(model_slug: str, region: str = "on") -> str:
    """Keyword-based AutoTrader search URL in a province."""
    return f"{BASE_URL}/motorcycles-atvs/lst/reg_{region}?loc=Ontario&kwd={quote(model_slug)}"


def _parse_price(text: str) -> tuple:
    if not text:
        return None, PRICE_STATE_UNKNOWN
    match = re.search(r"[\d,]+", text.replace(",", ""))
    if not match:
        return None, PRICE_STATE_UNKNOWN
    try:
        return float(match.group().replace(",", "")), PRICE_STATE_NUMERIC
    except ValueError:
        return None, PRICE_STATE_UNKNOWN


def fetch_page(url: str, timeout: int = 20) -> str:
    time.sleep(RATE_LIMIT_SECONDS + random.uniform(0, 0.8))
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return str(response.text)


def parse_listings(html: str, model_slug: str) -> list[Listing]:
    """Extract AutoTrader listing cards. Selectors are best-effort."""
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.utcnow().isoformat()
    results = []

    # AutoTrader uses React-rendered cards. Common selectors to try:
    cards = (
        soup.select("div.result-item")
        or soup.select("div[class*='ResultItem']")
        or soup.select("article[data-testid*='listing']")
    )

    for card in cards:
        link_el = card.find("a", href=True)
        if not link_el:
            continue
        href = str(link_el["href"])
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        title_el = card.find(["h2", "h3", "span"], class_=re.compile(r"title", re.I))
        title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)

        price_el = card.find(class_=re.compile(r"price", re.I))
        price_text = price_el.get_text(strip=True) if price_el else ""
        price, state = _parse_price(price_text)

        loc_el = card.find(class_=re.compile(r"location|proximity", re.I))
        location = loc_el.get_text(strip=True) if loc_el else ""

        detected = detect_models_in_title(title)
        if model_slug not in detected and model_slug.lower() not in title.lower():
            continue

        external_id = re.search(r"([a-f0-9-]{36}|\d{8,})", url)
        results.append(
            Listing(
                source="autotrader",
                external_id=external_id.group() if external_id else url,
                url=url,
                title=title,
                price=price,
                price_state=state,
                location=location,
                posted_text="",
                description="",
                model_slugs=detected if detected else [model_slug],
                scraped_at=now,
            )
        )

    return results


def scrape_model(model_slug: str, region: str = "on", max_pages: int = 1) -> list[Listing]:  # noqa: ARG001
    """Scrape AutoTrader keyword search for a model."""
    url = build_search_url(model_slug, region)
    try:
        html = fetch_page(url)
    except requests.RequestException as exc:
        print(f"AutoTrader fetch failed: {exc}")  # noqa: T201
        return []
    return parse_listings(html, model_slug)
