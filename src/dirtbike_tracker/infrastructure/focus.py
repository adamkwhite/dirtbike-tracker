"""Dealer sites on the Focus platform, e.g. Del Mastro Recreation (Peterborough).

The inventory page renders client-side from a Typesense index the dealer proxies at
<base>/ts, queried with a search-only key embedded in that page. We read the key from
the page each run (so a rotated key keeps working), then ask the index for every used
motorcycle and powersports unit in one request: clean JSON with year, name, price,
odometer and photo. Like Dealer Spike (#99), one fetch is the dealer's whole stock, so
each model filters it by title and `last_fetch_ok` tells watch the dealer answered.
"""

import random
import re
from datetime import UTC, datetime
from functools import lru_cache

import requests

from ..domain.listing import Listing
from ..domain.scoring import parse_km, parse_year
from .kijiji_scraper import PRICE_STATE_NUMERIC, PRICE_STATE_UNKNOWN, USER_AGENTS, model_slugs_for

PHOTO_URL = "https://dealers.focus-static.com/inventory/{dealer}/w800/{image}"
CATEGORIES = "[Motorcycles,Powersports]"
PER_PAGE = 250  # Typesense's max; Del Mastro had 97 such units, new and used (2026-10-16)
_KEY_RE = re.compile(r"apiKey:\s*'([A-Za-z0-9]+)'")


def _headers() -> dict:
    return {"User-Agent": random.choice(USER_AGENTS)}  # NOSONAR - UA rotation, not security


def search_key(page_html: str) -> str | None:
    """The search-only Typesense key the inventory page embeds."""
    m = _KEY_RE.search(page_html)
    return m.group(1) if m else None


class FocusSource:
    """A ListingSource over one Focus dealer's used motorcycles and powersports."""

    def __init__(self, name: str, base_url: str, location: str) -> None:
        self.name = name
        self.base_url = base_url
        self.location = location
        self.last_fetch_ok = False  # read by watch.sources_seen, as for Dealer Spike

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
            docs = _inventory(self.base_url)
        except (requests.RequestException, ValueError) as exc:  # ValueError: no key, bad JSON
            print(f"{self.name}: inventory fetch failed: {exc}")  # noqa: T201
            self.last_fetch_ok = False
            return []
        self.last_fetch_ok = bool(docs)
        listings = self.parse(list(docs), model_slug)
        print(f"{self.name} {model_slug}: {len(listings)} listings")  # noqa: T201
        return listings

    def parse(self, docs: list[dict], model_slug: str) -> list[Listing]:
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        listings = []
        for d in docs:
            title = str(d.get("name") or "")
            slugs = model_slugs_for(title, model_slug)
            # sold: 1 sold, 2 deal pending
            if d.get("condition") != "Used" or d.get("sold") or not slugs or not d.get("id"):
                continue
            price = float(d.get("price") or 0)
            image = d.get("image")
            listings.append(
                Listing(
                    source=self.name,
                    external_id=str(d["id"]),
                    url=f"{self.base_url}/inventory/{d.get('slug', '')}",
                    title=title,
                    price=price or None,
                    price_state=PRICE_STATE_NUMERIC if price else PRICE_STATE_UNKNOWN,
                    location=self.location,
                    posted_text="",
                    description=str(d.get("callout_msg") or ""),
                    model_slugs=slugs,
                    scraped_at=now,
                    year=parse_year(d.get("model_year"), int(now[:4])),
                    km=parse_km(d.get("odometer")),
                    image_url=PHOTO_URL.format(dealer=d.get("dealer_id"), image=image)
                    if image
                    else None,
                    seller_type="dealer",
                )
            )
        return listings


# ponytail: per-process cache, like dealerspike._fetch: every model filters the same
# result, so a watch run asks the dealer once. A failed fetch raises, which isn't kept.
@lru_cache(maxsize=4)
def _inventory(base_url: str, timeout: int = 20) -> tuple[dict, ...]:
    page = requests.get(f"{base_url}/inventory/", headers=_headers(), timeout=timeout)
    page.raise_for_status()
    key = search_key(page.text)
    if not key:
        raise ValueError("no Typesense search key on the inventory page")
    search = {
        "collection": "vehicles",
        "q": "*",
        "query_by": "name",
        "per_page": PER_PAGE,
        "filter_by": f"category:={CATEGORIES}",
    }
    response = requests.post(
        f"{base_url}/ts/multi_search",
        params={"x-typesense-api-key": key},
        json={"searches": [search]},
        headers=_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()["results"][0]
    return tuple(hit["document"] for hit in result.get("hits", []))


DEL_MASTRO = FocusSource("delmastro", "https://www.delmastro.ca", location="Cavan Monaghan, ON")
