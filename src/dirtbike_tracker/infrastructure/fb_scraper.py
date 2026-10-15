"""Facebook Marketplace source (#9), via Playwright.

Marketplace renders listings with React, so a real browser loads the page and
we parse the rendered HTML. Logged-out search returns listings (a login overlay
sits on top), so this works without an account; `dirtbike fb-login` saves a
session into the browser profile for when FB starts requiring one.

FB rate-limits and bans scrapers, and automated access is against its terms:
each model is searched at most once per MIN_INTERVAL, and only models that list
"facebook" in their models.yml sources. Playwright is optional (pip install -e
'.[fb]'); without it this source logs why and returns nothing.
"""

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..domain.listing import Listing
from ..domain.scoring import parse_km, parse_year
from .kijiji_scraper import (
    PRICE_STATE_NUMERIC,
    PRICE_STATE_UNKNOWN,
    detect_models_in_title,
    first_image,
    model_slugs_for,
)

BASE_URL = "https://www.facebook.com"
# Marketplace's city slug; FB picks the radius around it
CITY = os.environ.get("FB_CITY", "toronto")
PROFILE_DIR = Path(
    os.environ.get("FB_PROFILE_DIR", "~/.config/dirtbike-tracker/fb-profile")
).expanduser()
MIN_INTERVAL = timedelta(hours=6)  # per model: ~4 searches a day
RENDER_WAIT_MS = 6000

_PRICE_RE = re.compile(r"^(?:CA)?\$\s?([\d,]+)$")
_KM_RE = re.compile(r"^([\d.,]+)(K?) ?km$", re.I)  # "12K km", "12,000 km"
_YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-3]\d)\b")
_WANTED_RE = re.compile(r"\b(wanted|wtb|iso|looking for)\b", re.I)
# Street bikes draw accessory ads. A title with one of these words, no year and a parts price
# is a part ("Luggage racks for CB500X", $280); with a year or a bike's price it's a bike that
# has one ("2014 CB500F, new tires", "KLX 140 G big wheel version" at $3,200).
ACCESSORY_MAX_PRICE = 400  # accessory ads seen: $45-$280; yearless project bikes start around $450
_ACCESSORY_RE = re.compile(
    r"\b(racks?|luggage|saddlebags?|bags?|panniers?|supports?|brackets?|mounts?|exhaust|"
    r"muffler|seat|windscreen|windshield|mirrors?|tires?|wheels?|rims?|covers?|guards?|"
    r"helmets?|jackets?|kits?|lowering)\b",
    re.I,
)
_BADGES = {"just listed", "sold", "pending"}  # card status lines, not the title
# FB requires a price, so "no price" ads use one of these (1 and 123 are under MIN_BIKE_PRICE)
_PLACEHOLDER_PRICES = {1234, 12345, 123456}

Fetch = Callable[[str], str]  # search url -> rendered html


def build_search_url(model_slug: str, city: str = CITY) -> str:
    return f"{BASE_URL}/marketplace/{city}/search/?query={quote(model_slug)}"


def _km(line: str) -> int | None:
    m = _KM_RE.match(line)
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    return parse_km(int(number * 1000) if m.group(2) else int(number))


def _card_to_listing(
    item_id: str, lines: list[str], image: str | None, model_slug: str, now: str
) -> Listing | None:
    """A card's text lines are: price, [previous price], title, location, [mileage]."""
    prices = [int(m.group(1).replace(",", "")) for line in lines if (m := _PRICE_RE.match(line))]
    prices = [p for p in prices if p not in _PLACEHOLDER_PRICES]
    rest = [
        line
        for line in lines
        if not _PRICE_RE.match(line) and line.lower() not in {"free", *_BADGES}
    ]
    if not rest:
        return None
    title, location = rest[0], (rest[1] if len(rest) > 1 else "")
    # Kijiji and AutoTrader are Ontario-only; FB's radius reaches into New York
    if _WANTED_RE.search(title) or (location and not location.endswith(", ON")):
        return None
    year_match = _YEAR_RE.search(title)
    parts_price = not prices or prices[0] < ACCESSORY_MAX_PRICE
    if _ACCESSORY_RE.search(title) and not year_match and parts_price:
        return None
    # FB's search already matched the model, so keep vague titles ("2003 Honda crf")
    # that still name its family; drop those naming a different model, known or not:
    # "klx 125L" names a KLX125, not the klx140 we searched for.
    slug_parts = re.match(r"([a-z]+)(\d*)", model_slug)
    family, number = slug_parts.groups() if slug_parts else (model_slug, "")
    # up to 3 digits, so the year in "Yamaha 125 TTR 2010" isn't read as a model number
    named = re.search(rf"\b{family}\s*(\d{{1,3}}(?!\d))?", title.lower().replace("-", ""))
    vague_ok = (
        not detect_models_in_title(title) and named is not None and named.group(1) in (None, number)
    )
    slugs = model_slugs_for(title, model_slug) or ([model_slug] if vague_ok else [])
    if not slugs:
        return None
    return Listing(
        source="facebook",
        external_id=item_id,
        url=f"{BASE_URL}/marketplace/item/{item_id}/",
        title=title,
        price=float(prices[0]) if prices else None,
        price_state=PRICE_STATE_NUMERIC if prices else PRICE_STATE_UNKNOWN,
        location=location,
        posted_text="",
        description="",
        model_slugs=slugs,
        scraped_at=now,
        year=parse_year(year_match.group(1), datetime.now(UTC).year) if year_match else None,
        km=next(filter(None, map(_km, rest[2:])), None),
        image_url=image,
    )


def parse_listings(html: str, model_slug: str) -> list[Listing]:
    """Listings from a rendered Marketplace search page (cards are /marketplace/item/<id>/ links)."""
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    listings: dict[str, Listing] = {}
    for link in soup.select('a[href*="/marketplace/item/"]'):
        m = re.search(r"/marketplace/item/(\d+)", str(link["href"]))
        if not m or m.group(1) in listings:
            continue
        image = first_image([str(img["src"]) for img in link.select("img[src]")])
        listing = _card_to_listing(m.group(1), list(link.stripped_strings), image, model_slug, now)
        if listing:
            listings[m.group(1)] = listing
    return list(listings.values())


def fetch_search_page(url: str, profile_dir: Path = PROFILE_DIR, headless: bool = True) -> str:
    """Render a search page in Chromium with the saved profile (logged in or not)."""
    from playwright.sync_api import sync_playwright  # optional dependency

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # ponytail: one browser per search; share one per watch run if FB runs many models
        context = p.chromium.launch_persistent_context(
            str(profile_dir), headless=headless, locale="en-CA"
        )
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(RENDER_WAIT_MS)
            return str(page.content())
        finally:
            context.close()


def login(profile_dir: Path = PROFILE_DIR) -> None:
    """Open a visible browser on the FB login page; the session is saved when you close it."""
    from playwright.sync_api import sync_playwright  # optional dependency

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir), headless=False, locale="en-CA"
        )
        page = context.new_page()
        page.goto(f"{BASE_URL}/login")
        page.wait_for_event("close", timeout=0)  # until you close the window
        context.close()


class FacebookSource:
    """The ListingSource for Facebook, throttled per model to MIN_INTERVAL.

    A throttled or failed search returns [] (the source-health check alerts if FB
    returns nothing for 24h). Last-search times live in `state_path`.
    """

    def __init__(
        self,
        state_path: Path,
        fetch: Fetch = fetch_search_page,
        min_interval: timedelta = MIN_INTERVAL,
    ) -> None:
        self.state_path = state_path
        self.fetch = fetch
        self.min_interval = min_interval

    # FB has one results page, no region search and no new/used flag
    def __call__(
        self,
        model_slug: str,
        region: str,  # noqa: ARG002
        pages: int,  # noqa: ARG002
        *,
        query: str | None = None,
        include_new: bool = False,  # noqa: ARG002
    ) -> list[Listing]:
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        now = datetime.now(UTC)
        last = state.get(model_slug)
        if last and now - datetime.fromisoformat(last) < self.min_interval:
            return []
        try:
            html = self.fetch(build_search_url(query or model_slug))
        except ImportError:
            print("facebook: Playwright not installed (pip install -e '.[fb]'), skipping")  # noqa: T201
            return []
        # a browser crash, timeout or FB block skips this run; the next run retries
        except Exception as exc:  # noqa: BLE001
            print(f"facebook: {model_slug} search failed: {exc}")  # noqa: T201
            return []
        state[model_slug] = now.isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=1))
        listings = parse_listings(html, model_slug)
        print(f"facebook {model_slug}: {len(listings)} listings")  # noqa: T201
        return listings
