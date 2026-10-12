"""
Kijiji dirt bike listing scraper.

Fetches search results for a given model/region, parses listings,
returns structured data. Respects rate limits and rotates User-Agent.
"""

import json
import random
import re
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup, Tag

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

BASE_URL = "https://www.kijiji.ca"
RATE_LIMIT_SECONDS = 2.5


# Price-state classifications beyond raw numeric price
PRICE_STATE_NUMERIC = "numeric"
PRICE_STATE_CONTACT = "please_contact"
PRICE_STATE_TRADE = "swap_trade"
PRICE_STATE_UNKNOWN = "unknown"


@dataclass
class Listing:
    """A single Kijiji listing."""

    source: str
    external_id: str
    url: str
    title: str
    price: float | None
    price_state: str  # numeric | please_contact | swap_trade | unknown
    location: str
    posted_text: str
    description: str
    model_slugs: list[str]  # one listing can match multiple models (e.g. a package)
    scraped_at: str
    full_description: str | None = None  # set when the search page already had the whole ad (#76)

    def to_dict(self):
        return asdict(self)


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),  # NOSONAR - UA rotation, not security
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _parse_price(text: str) -> tuple[float | None, str]:
    """Extract numeric price and classify its state.

    Returns (price_or_none, state) where state is one of:
      numeric       - we got a real number
      please_contact - seller said "Please Contact"
      swap_trade    - seller only wants trade
      unknown       - couldn't parse anything
    """
    if not text:
        return None, PRICE_STATE_UNKNOWN

    normalized = text.lower().strip()
    if "please contact" in normalized or "contact for" in normalized:
        return None, PRICE_STATE_CONTACT
    if "swap" in normalized or "trade" in normalized:
        return None, PRICE_STATE_TRADE

    match = re.search(r"[\d,]+(?:\.\d{2})?", text.replace(",", ""))
    if not match:
        return None, PRICE_STATE_UNKNOWN
    try:
        return float(match.group().replace(",", "")), PRICE_STATE_NUMERIC
    except ValueError:
        return None, PRICE_STATE_UNKNOWN


# Known model slugs we can detect in listing titles. Order doesn't matter.
# Add new ones here as you scrape new models.
KNOWN_MODEL_SLUGS = [
    "crf50f",
    "crf80f",
    "crf100f",
    "crf125f",
    "crf150f",
    "crf230f",
    "crf250f",
    "crf250r",
    "crf250l",
    "crf450r",
    "ttr50",
    "ttr110",
    "ttr125",
    "ttr125le",
    "ttr225",
    "ttr230",
    "yz80",
    "yz85",
    "yz125",
    "yz250",
    "kx65",
    "kx80",
    "kx85",
    "kx100",
    "kx125",
    "kx250f",
    "klx110",
    "klx140",
    "klx230",
    "klx300",
    "rm80",
    "rm85",
    "rmz250",
    "xr50",
    "xr70",
    "xr75",
    "xr80",
    "xr100",
    "xr200",
    "xr250",
    "dr100",
    "dr125",
    "dr200",
    "dr350",
]


_SEPARATORS = re.compile(r"[\s\-_]+")


def detect_models_in_title(title: str) -> list[str]:
    """Find every known model slug mentioned in a listing title."""
    if not title:
        return []
    normalized = _SEPARATORS.sub("", title.lower())
    found = []
    for slug in KNOWN_MODEL_SLUGS:
        if slug in normalized:
            found.append(slug)
    return found


def _extract_external_id(url: str) -> str:
    """Pull the Kijiji ad ID out of a listing URL."""
    match = re.search(r"/(\d{10,})(?:[/?#]|$)", url)
    return str(match.group(1)) if match else url


def build_search_url(model_slug: str, region: str = "ontario") -> str:
    """Build a Kijiji search URL for a dirt bike model in a region.

    Kijiji's URL structure uses the category slug in the path.
    Ontario location code is l9004. No category ID needed. Kijiji
    scopes to the slug (b-dirt-bikes-motocross) automatically.
    """
    query = quote(model_slug)
    return f"{BASE_URL}/b-dirt-bikes-motocross/{region}/{query}/k0l9004"


# Only accept listings whose URL contains one of these category paths.
# Dirt bikes get cross-posted to adjacent categories, so allow those too.
ALLOWED_CATEGORY_PATHS = (
    "/v-dirt-bikes-motocross/",
    "/v-atv-snowmobile-other/",
    "/v-sport-other/",
    "/v-street-cruisers-choppers/",
    "/v-sport-touring-motorcycles/",
    "/v-other-motorcycles/",
    "/v-motocross-other/",
)


def fetch_page(url: str, timeout: int = 20) -> str:
    """Fetch a single page's HTML with rotating UA and rate limit."""
    time.sleep(RATE_LIMIT_SECONDS + random.uniform(0, 1.0))
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return str(response.text)


def fetch_ad_description(url: str) -> str | None:
    """Full description from an ad page. None on any fetch or parse failure."""
    try:
        html = fetch_page(url)
    except requests.RequestException:
        return None
    return parse_ad_description(html, _extract_external_id(url))


def parse_ad_description(html: str, ad_id: str) -> str | None:
    """The ad's description from its page's __NEXT_DATA__ Apollo cache.

    Keyed like "StandardListing:<id>" (verified 2026-10-12); match any type prefix.
    """
    script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    try:
        cache = json.loads(script.get_text())["props"]["pageProps"]["__APOLLO_STATE__"]  # type: ignore[union-attr]
    except (AttributeError, ValueError, KeyError, TypeError):
        return None
    for key, entry in cache.items():
        if key.endswith(f":{ad_id}") and isinstance(entry, dict):
            text = entry.get("description")
            if isinstance(text, str):
                return text
    return None


def _iter_ads(data: object) -> Iterator[dict]:
    """Every dict with an id anywhere in the __NEXT_DATA__ tree."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if node.get("id"):
                yield node
            stack.extend(node.values())


def _is_new_vehicle(ad: dict) -> bool:
    attrs = ad.get("attributes")
    if isinstance(attrs, dict):
        attrs = attrs.get("all")
    if not isinstance(attrs, list):
        return False
    return any(
        isinstance(a, dict)
        and a.get("canonicalName") == "vehicletype"
        and "new" in (a.get("canonicalValues") or [])
        for a in attrs
    )


# Search-page descriptions are cut at ~200 chars and end like this; shorter ones are the whole ad.
TRUNCATION_MARK = "..."


def _ad_meta(soup: BeautifulSoup) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Per-ad facts from the page's __NEXT_DATA__ the HTML cards don't show.

    Returns (ids marked vehicletype=new, {id: activationDate}, {id: description}).
    New bikes are dealer MSRP stock and drag the median up; activationDate is the
    original post time (sortingDate moves when an ad is bumped); the description
    is a ~200-char snippet, where the cards mostly have none. Missing or reshaped
    data returns empties, so a Kijiji markup change drops nothing.
    """
    script = soup.find("script", id="__NEXT_DATA__")
    try:
        data = json.loads(script.get_text()) if script else None
    except ValueError:
        return set(), {}, {}

    new_ids: set[str] = set()
    posted: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for ad in _iter_ads(data):
        ad_id = str(ad["id"])
        if _is_new_vehicle(ad):
            new_ids.add(ad_id)
        if isinstance(ad.get("activationDate"), str):
            posted[ad_id] = ad["activationDate"]
        if isinstance(ad.get("description"), str) and ad["description"].strip():
            descriptions[ad_id] = ad["description"].strip()
    return new_ids, posted, descriptions


def _field_text(card: Tag, testid: str, class_pattern: str | None = None, sep: str = "") -> str:
    """Text of the card element whose data-testid (else class) matches, or ''."""
    el = card.find(attrs={"data-testid": re.compile(testid, re.I)}) or card.find(  # type: ignore[call-overload]
        class_=re.compile(class_pattern or testid, re.I)
    )
    return el.get_text(sep, strip=True) if isinstance(el, Tag) else ""


def parse_listings(html: str, model_slug: str) -> list[Listing]:
    """Extract listing data from a Kijiji search results page."""
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    new_ids, posted_at, snippets = _ad_meta(soup)

    # Kijiji's current markup uses [data-testid='listing-card'] but
    # fall back to older selectors for resilience.
    cards = (
        soup.select("[data-testid='listing-card']")
        or soup.select("section[data-testid*='listing']")
        or soup.select("div.search-item")
    )

    for card in cards:
        link_el = card.find("a", href=True)
        if not link_el:
            continue
        url = urljoin(BASE_URL, str(link_el["href"]))
        ad_id = _extract_external_id(url)
        # Drop unrelated Kijiji categories and new dealer stock
        if not any(path in url for path in ALLOWED_CATEGORY_PATHS) or ad_id in new_ids:
            continue

        title_el = card.find(["h3", "h2", "a"], string=True)
        title = (title_el or link_el).get_text(strip=True) or ""
        model_slugs = _model_slugs(title, model_slug)
        if not model_slugs:
            continue
        price, price_state = _parse_price(_field_text(card, "price"))
        location = _field_text(card, "location")
        posted = posted_at.get(ad_id) or _field_text(card, "date", r"date|posted")
        snippet = snippets.get(ad_id)
        description = snippet or _field_text(card, "description", sep=" ")

        listings.append(
            Listing(
                source="kijiji",
                external_id=ad_id,
                url=url,
                title=title,
                price=price,
                price_state=price_state,
                location=location,
                posted_text=posted,
                description=description,
                model_slugs=model_slugs,
                scraped_at=now,
                full_description=(
                    snippet if snippet and not snippet.endswith(TRUNCATION_MARK) else None
                ),
            )
        )

    return listings


def _model_slugs(title: str, model_slug: str) -> list[str]:
    """Models a title belongs to, or [] if it doesn't plausibly match the queried slug.

    Packages can name several models. A title that only passes the loose check
    (older slug variants) gets the queried slug alone.
    """
    detected = detect_models_in_title(title)
    if model_slug in detected:
        return detected
    if _title_matches_model(title, model_slug):
        return detected or [model_slug]
    return []


def _title_matches_model(title: str, model_slug: str) -> bool:
    """Loose check that a listing title mentions the model."""
    if not title:
        return False
    normalized_title = _SEPARATORS.sub("", title.lower())
    normalized_slug = _SEPARATORS.sub("", model_slug.lower())
    return normalized_slug in normalized_title


def scrape_model(model_slug: str, region: str = "ontario", max_pages: int = 3) -> list[Listing]:
    """Scrape N pages of listings for one model in one region."""
    all_listings = []
    base = build_search_url(model_slug, region)
    for page in range(1, max_pages + 1):
        url = base if page == 1 else f"{base}/page-{page}"
        try:
            html = fetch_page(url)
        except requests.HTTPError as exc:
            print(f"Page {page}: HTTP {exc.response.status_code}, stopping.")  # type: ignore[union-attr] # noqa: T201
            break
        except requests.RequestException as exc:
            print(f"Page {page}: {exc}, stopping.")  # noqa: T201
            break

        page_listings = parse_listings(html, model_slug)
        if not page_listings:
            break
        all_listings.extend(page_listings)
        print(f"Page {page}: {len(page_listings)} listings")  # noqa: T201

    return all_listings


if __name__ == "__main__":
    import sys

    model = sys.argv[1] if len(sys.argv) > 1 else "crf150f"
    results = scrape_model(model)
    print(f"\nTotal: {len(results)} listings for {model}")  # noqa: T201
    for r in results[:5]:
        print(f"  {r.title} | {r.price} | {r.location}")  # noqa: T201
