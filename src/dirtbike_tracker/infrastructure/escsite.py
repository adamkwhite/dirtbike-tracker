"""Dealer sites on the EquipmentSearch platform, e.g. Cycle World Superstore in Scarborough.

Used stock is server-rendered at <base>/Pre-Owned/page/<n>/, one `div.item` card per unit
(the link /Pre-Owned/adid/<id>/..., price, and Year / Make / Model / Km spec fields), with
"Page 1 of 3" giving the page count. One crawl is the dealer's whole used inventory, so,
like Dealer Spike (#99), each model filters it by title and `last_fetch_ok` tells watch a
dealer with none of a model answered.
"""

import random
import re
from datetime import UTC, datetime
from functools import lru_cache

import requests
from bs4 import BeautifulSoup, Tag

from ..domain.listing import Listing
from ..domain.scoring import is_dealer_new, parse_km, parse_year
from .kijiji_scraper import PRICE_STATE_NUMERIC, PRICE_STATE_UNKNOWN, USER_AGENTS, model_slugs_for

MAX_PAGES = 10  # 20 units a page; Cycle World had 3 pages (2026-10-16)
_PAGES_RE = re.compile(r"Page \d+ of (\d+)")


def _spec(card: Tag, name: str) -> str:
    """A spec field's value: "Year 2015" -> "2015"."""
    col = card.select_one(f".spec-col.{name}")
    if not col:
        return ""
    label = col.select_one(".specs-title")
    text = str(col.get_text(" ", strip=True))
    return text.removeprefix(label.get_text(strip=True)).strip() if label else text


def parse_cards(html: str) -> list[dict]:
    """The units on one inventory page, as plain fields. [] if the cards changed shape."""
    units = []
    for card in BeautifulSoup(html, "html.parser").select("div.item.list-group-item"):
        link = card.select_one('a.model-title-link[href*="/adid/"]')
        if link is None:
            continue
        href = str(link.get("href", "")).split("#")[0]
        m = re.search(r"/adid/(\d+)/", href)
        if not m:
            continue
        price = card.select_one(".price.show-item .price-val")
        image = card.select_one('img[src*="/uploadedimages/"]:not([src*="logos"])')
        year, model = _spec(card, "year"), _spec(card, "model")
        # some units' Model already reads "2008 Suzuki C50T Boulevard"
        parts = (model,) if year and model.startswith(year) else (year, _spec(card, "make"), model)
        title = " ".join(p for p in parts if p)
        units.append(
            {
                "id": m.group(1),
                "path": href,
                "title": title or link.get_text(" ", strip=True),
                "year": year,
                "km": _spec(card, "odometer").removesuffix("km").strip(),
                "price": "" if price is None else price.get_text(strip=True),
                "image": None if image is None else str(image.get("src")),
            }
        )
    return units


def page_count(html: str) -> int:
    m = _PAGES_RE.search(BeautifulSoup(html, "html.parser").get_text(" "))
    return min(int(m.group(1)), MAX_PAGES) if m else 1


class EscSource:
    """A ListingSource over one EquipmentSearch dealer's used inventory (seller_type dealer)."""

    def __init__(self, name: str, base_url: str, location: str, path: str = "/Pre-Owned/") -> None:
        self.name = name
        self.base_url = base_url
        self.location = location
        self.path = path
        # one crawl is the whole inventory: a crawl that worked and matched nothing means
        # the dealer has none of the model (sold), not a broken scraper. watch reads this
        self.last_fetch_ok = False

    def __call__(
        self,
        model_slug: str,
        region: str,  # noqa: ARG002
        pages: int,  # noqa: ARG002
        *,
        query: str | None = None,  # noqa: ARG002
        include_new: bool = False,  # noqa: ARG002 - used stock only
    ) -> list[Listing]:
        try:
            units = _inventory(self.base_url + self.path)
        except requests.RequestException as exc:
            print(f"{self.name}: inventory fetch failed: {exc}")  # noqa: T201
            self.last_fetch_ok = False
            return []
        self.last_fetch_ok = bool(units)
        listings = self.parse(list(units), model_slug)
        print(f"{self.name} {model_slug}: {len(listings)} listings")  # noqa: T201
        return listings

    def parse(self, units: list[dict], model_slug: str) -> list[Listing]:
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        this_year = int(now[:4])
        listings = []
        for u in units:
            year = parse_year(u["year"], this_year)
            slugs = model_slugs_for(u["title"], model_slug)
            if not slugs or is_dealer_new("dealer", year, this_year):
                continue
            price = re.sub(r"[^\d]", "", u["price"].split("CAD")[0])
            listings.append(
                Listing(
                    source=self.name,
                    external_id=u["id"],
                    url=self.base_url + u["path"],
                    title=u["title"],
                    price=float(price) if price and int(price) else None,
                    price_state=PRICE_STATE_NUMERIC
                    if price and int(price)
                    else PRICE_STATE_UNKNOWN,
                    location=self.location,
                    posted_text="",
                    description="",
                    model_slugs=slugs,
                    scraped_at=now,
                    year=year,
                    km=parse_km(u["km"]),
                    image_url=u["image"],
                    seller_type="dealer",
                )
            )
        return listings


def fetch_page(url: str, timeout: int = 20) -> str:
    headers = {"User-Agent": random.choice(USER_AGENTS)}  # NOSONAR - UA rotation, not security
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return str(response.text)


# ponytail: per-process cache, like dealerspike._fetch: every model filters the same crawl,
# so a watch run fetches each page once. A failed fetch raises, which lru_cache doesn't keep.
@lru_cache(maxsize=4)
def _inventory(url: str) -> tuple[dict, ...]:
    first = fetch_page(url)
    units = parse_cards(first)
    for n in range(2, page_count(first) + 1):
        units += parse_cards(fetch_page(f"{url}page/{n}/"))
    return tuple(units)


CYCLE_WORLD = EscSource(
    "cycleworld", "https://cycleworldsuperstore.com", location="Scarborough, ON"
)
