"""Dealer Spike dealer inventory (#99), e.g. Sturgess Cycle in Hamilton.

Dealer Spike sites render their inventory list client-side from a static cache
file holding every unit as a JS literal: `var Vehicles=[{...}, ...];`.
UVehInv.js is used stock, VehInv.js everything, under
/imglib/Inventory/cache/<site id>/. One GET returns the whole inventory, so
there's no search or paging: each model filters it by title.
"""

import json
import random
import re
from datetime import UTC, datetime
from functools import lru_cache

import requests

from ..domain.listing import Listing
from ..domain.scoring import is_dealer_new, parse_km, parse_year
from .kijiji_scraper import PRICE_STATE_NUMERIC, PRICE_STATE_UNKNOWN, USER_AGENTS, model_slugs_for

PHOTO_CDN = "https://cdn.dealerspike.com/imglib/v1/300x225/imglib/Assets/Inventory"
_TRADEMARKS = re.compile(r"[®™]")


def parse_vehicles(text: str) -> list[dict]:
    """The `Vehicles` array from a cache file. [] if the file changed shape."""
    start = text.find("var Vehicles=")
    if start < 0:
        return []
    try:
        vehicles, _ = json.JSONDecoder().raw_decode(text[start + len("var Vehicles=") :])
    except ValueError:
        return []
    return [v for v in vehicles if isinstance(v, dict)] if isinstance(vehicles, list) else []


def _photo(vehicle: dict) -> str | None:
    guid = str(vehicle.get("bike_image") or "").removesuffix(".jpg")
    return f"{PHOTO_CDN}/{guid[:2]}/{guid[2:4]}/{guid}.jpg" if len(guid) > 4 else None


def _posted(vehicle: dict) -> str:
    try:
        added = datetime.fromtimestamp(int(vehicle.get("DateAdded") or ""), UTC)
    except ValueError:
        return ""
    return added.replace(tzinfo=None).isoformat()


class DealerSpikeSource:
    """A ListingSource over one Dealer Spike dealer's inventory file.

    All units are dealer stock (seller_type "dealer"), so they don't set the
    private-sales median (#81). New units and a dealer's current-model-year bikes
    are skipped like Kijiji/AutoTrader new stock, unless a model sets `include_new`.
    """

    def __init__(self, name: str, base_url: str, site_id: int, location: str) -> None:
        self.name = name
        self.base_url = base_url
        self.site_id = site_id
        self.location = location
        # One fetch is the whole inventory, so a fetch that worked and matched nothing means the
        # dealer has none of the model (sold), not a broken scraper. watch reads this.
        self.last_fetch_ok = False

    def inventory_url(self, include_new: bool = False) -> str:
        file = "VehInv.js" if include_new else "UVehInv.js"
        return f"{self.base_url}/imglib/Inventory/cache/{self.site_id}/{file}"

    def __call__(
        self,
        model_slug: str,
        region: str,  # noqa: ARG002
        pages: int,  # noqa: ARG002
        *,
        query: str | None = None,  # noqa: ARG002
        include_new: bool = False,
    ) -> list[Listing]:
        try:
            text = _fetch(self.inventory_url(include_new))
        except requests.RequestException as exc:
            print(f"{self.name}: inventory fetch failed: {exc}")  # noqa: T201
            self.last_fetch_ok = False
            return []
        self.last_fetch_ok = bool(parse_vehicles(text))
        listings = self.parse(text, model_slug, include_new)
        print(f"{self.name} {model_slug}: {len(listings)} listings")  # noqa: T201
        return listings

    def parse(self, text: str, model_slug: str, include_new: bool = False) -> list[Listing]:
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        this_year = now[:4]
        listings = []
        for v in parse_vehicles(text):
            year = parse_year(v.get("bike_year"), int(this_year))
            is_new = v.get("type") == "N" or is_dealer_new("dealer", year, int(this_year))
            if is_new and not include_new:
                continue
            make = _TRADEMARKS.sub("", str(v.get("manuf") or "")).strip().title()
            model = _TRADEMARKS.sub("", str(v.get("model") or "")).strip()
            title = " ".join(str(p) for p in (v.get("bike_year"), make, model) if p)
            slugs = model_slugs_for(title, model_slug)
            if not slugs or not v.get("id"):
                continue
            price = str(v.get("price") or "").replace(",", "")
            page = "xNewInventoryDetail" if v.get("type") == "N" else "xPreOwnedInventoryDetail"
            listings.append(
                Listing(
                    source=self.name,
                    external_id=str(v["id"]),
                    url=f"{self.base_url}/default.asp?page={page}&id={v['id']}",
                    title=title,
                    price=float(price) if price.isdigit() and int(price) else None,
                    price_state=PRICE_STATE_NUMERIC if price.isdigit() else PRICE_STATE_UNKNOWN,
                    location=self.location,
                    posted_text=_posted(v),
                    description=str(v.get("notes") or ""),
                    model_slugs=slugs,
                    scraped_at=now,
                    year=year,
                    km=parse_km(v.get("miles")),
                    image_url=_photo(v),
                    seller_type="dealer",
                )
            )
        return listings


# ponytail: per-process cache: every model filters the same file, so a watch run fetches it
# once (each cron run is a new process). A failed fetch raises, which lru_cache doesn't keep.
@lru_cache(maxsize=4)
def _fetch(url: str, timeout: int = 20) -> str:
    headers = {"User-Agent": random.choice(USER_AGENTS)}  # NOSONAR - UA rotation, not security
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return str(response.content.decode("utf-8-sig"))


STURGESS = DealerSpikeSource(
    "sturgess", "https://www.sturgesscycle.com", site_id=5620, location="Hamilton, ON"
)
