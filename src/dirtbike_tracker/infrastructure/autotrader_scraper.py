"""
AutoTrader.ca scraper: keyword search plus used-only model-family pages (#17).

AutoTrader has thin inventory for used off-road dirt bikes compared to
Kijiji. Sellers mostly file dirt bikes under a model family ("Honda CRF")
with the variant in free text, which the keyword search misses, so models
in FAMILY_PATHS also read that family's used-only pages.
"""

import json
import random
import time
from datetime import UTC, datetime
from functools import lru_cache, partial
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..domain.listing import Listing
from ..domain.scoring import is_dealer_new, parse_km, parse_year
from .kijiji_scraper import (
    PRICE_STATE_NUMERIC,
    PRICE_STATE_UNKNOWN,
    USER_AGENTS,
    first_image,
    model_slugs_for,
)

BASE_URL = "https://www.autotrader.ca"
RATE_LIMIT_SECONDS = 2.5


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),  # NOSONAR - UA rotation, not security
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
    }


# Slugs AutoTrader's keyword search only finds spelled another way ("rebel500" returns 0)
KEYWORDS = {
    "rebel500": "rebel 500",
    "mt07": "mt-07",
    "vstrom650": "v-strom 650",
    "versys650": "versys 650",
}


# Model-family pages (make/model paths) a slug's listings file under (#17). Several models share
# a family; the title filter assigns each ad to its model. Unknown paths silently return the
# whole make page, so check a new path with the pagePath guard before adding it.
HONDA_CRF = ["honda/crf"]
KAWASAKI_KLX = ["kawasaki/klx"]
FAMILY_PATHS = {
    "crf125f": HONDA_CRF,
    "crf150f": HONDA_CRF,
    "crf230f": HONDA_CRF,
    "crf250f": HONDA_CRF,
    "crf300l": HONDA_CRF,
    "ttr125": ["yamaha/ttr-125", "yamaha/tt-r125"],
    "klx125": KAWASAKI_KLX,
    "klx140": KAWASAKI_KLX,
    "klx300": KAWASAKI_KLX,
    "xr250": ["honda/xr"],
    "mt07": ["yamaha/mt-07"],
    "sv650": ["suzuki/sv-650"],
    "nc750x": ["honda/nc750x"],
    "vstrom650": ["suzuki/v-strom-650"],
    "vulcans": ["kawasaki/vulcan-s"],
    "versys650": ["kawasaki/versys-650"],
    "boulevard": ["suzuki/boulevard"],
    "drz125": ["suzuki/dr-z"],
}
MAX_FAMILY_PAGES = 3  # 20 ads a page; the used family pages are 1 page today


# listing.seller.type (#81)
SELLER_TYPES = {"PrivateSeller": "private", "Dealer": "dealer"}


def build_search_url(model_slug: str, region: str = "on", query: str | None = None) -> str:
    """Keyword-based AutoTrader search URL in a province."""
    keyword = query or KEYWORDS.get(model_slug, model_slug)
    return f"{BASE_URL}/motorcycles-atvs/lst/reg_{region}?loc=Ontario&kwd={quote(keyword)}"


def build_family_url(path: str, region: str = "on", page: int = 1) -> str:
    """Used-only listings in one model family ("honda/crf"), one results page."""
    url = f"{BASE_URL}/motorcycles-atvs/lst/{path}/reg_{region}/ot_used?loc=Ontario"
    return url if page == 1 else f"{url}&page={page}"


def fetch_page(url: str, timeout: int = 20) -> str:
    time.sleep(RATE_LIMIT_SECONDS + random.uniform(0, 0.8))
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return str(response.text)


# ponytail: per-process cache, so the 5 Honda models fetch honda/crf once per watch run (each
# cron run is a new process). A failed fetch raises, and lru_cache doesn't keep exceptions.
@lru_cache(maxsize=32)
def _fetch_cached(url: str) -> str:
    return fetch_page(url)


def _page_props(html: str) -> dict:
    """The search page's __NEXT_DATA__ props.pageProps, {} if missing or reshaped."""
    script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    try:
        props = json.loads(script.get_text())["props"]["pageProps"] if script else {}
    except (ValueError, KeyError, TypeError):
        return {}
    return props if isinstance(props, dict) else {}


def parse_listings(html: str, model_slug: str, include_new: bool = False) -> list[Listing]:
    """Extract listings from the search page's __NEXT_DATA__ (props.pageProps.listings).

    AutoTrader is a Next.js app; the rendered cards have no stable selectors,
    but the embedded page data does. New stock (offerType "N") is skipped:
    it's dealer MSRP, not used-market prices. `include_new` keeps it, for a
    model shopping new (models.yml).
    """
    ads = _page_props(html).get("listings")
    if not isinstance(ads, list):
        return []

    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    parsed = (_ad_to_listing(ad, model_slug, now, include_new) for ad in ads)
    return [listing for listing in parsed if listing is not None]


def _ad_to_listing(ad: dict, model_slug: str, now: str, include_new: bool) -> Listing | None:
    """One __NEXT_DATA__ ad -> Listing, or None for new stock / other models."""
    vehicle = ad.get("vehicle") or {}
    year = parse_year(vehicle.get("modelYear"), datetime.now(UTC).year)
    seller_type = SELLER_TYPES.get(str((ad.get("seller") or {}).get("type")))
    # new stock: marked offerType N, or a dealer's current-model-year bike (demo, #81)
    is_new = vehicle.get("offerType") == "N" or is_dealer_new(
        seller_type, year, datetime.now(UTC).year
    )
    if is_new and not include_new:
        return None

    parts = [vehicle.get("modelYear"), vehicle.get("make"), vehicle.get("model")]
    parts.append(vehicle.get("modelVersionInput"))
    title = " ".join(str(p) for p in parts if p)
    model_slugs = model_slugs_for(title, model_slug)  # "Speed Twin 1200" matches speedtwin1200
    if not model_slugs:
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
        model_slugs=model_slugs,
        scraped_at=now,
        year=year,
        km=parse_km(vehicle.get("mileageInKm")),
        image_url=first_image(ad.get("images")),
        seller_type=seller_type,
    )


def scrape_family(path: str, model_slug: str, region: str = "on") -> list[Listing]:
    """A model family's used-only pages, up to MAX_FAMILY_PAGES; [] if AutoTrader didn't
    recognize the path (it then serves the whole make page, seen only in pagePath)."""
    listings: list[Listing] = []
    for page in range(1, MAX_FAMILY_PAGES + 1):
        html = _fetch_cached(build_family_url(path, region, page))
        props = _page_props(html)
        if f"/{path}/" not in str(props.get("pagePath", "")):
            print(f"AutoTrader: {path} isn't a model family (got {props.get('pagePath')!r})")  # noqa: T201
            return []
        listings += parse_listings(html, model_slug)
        if page >= int(props.get("numberOfPages") or 1):
            break
    return listings


def scrape_model(
    model_slug: str, region: str = "on", *, query: str | None = None, include_new: bool = False
) -> list[Listing]:
    """Scrape AutoTrader for a model: keyword search, plus its used family pages (#17).

    The keyword search stays: it finds ads filed under the exact model, which a family page
    can miss. Results are deduped by ad id. Family pages are used-only, so a model shopping
    new (`include_new`) gets its new stock from the keyword search.
    """
    keyword_url = build_search_url(model_slug, region, query)
    searches = [lambda: parse_listings(fetch_page(keyword_url), model_slug, include_new)]
    searches += [
        partial(scrape_family, path, model_slug, region)
        for path in FAMILY_PATHS.get(model_slug, [])
    ]
    found: dict[str, Listing] = {}
    for search in searches:
        try:
            listings = search()
        except requests.RequestException as exc:
            print(f"AutoTrader fetch failed: {exc}")  # noqa: T201
            continue
        for listing in listings:
            found.setdefault(listing.external_id, listing)
    return list(found.values())
