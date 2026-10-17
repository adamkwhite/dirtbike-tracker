"""Dealer sites on ARI's site builder (Dealer Spike's newer platform), e.g. Burlington Cycle.

Search pages are server-rendered, 30 units a page at <base>/search/inventory/.../page/<n>,
each card carrying its unit as JSON in a hidden `span.datasource`: year, make, model,
`itemPrice` (the current all-in price), `unitPrice` (the regular one) and `itemOnSale`.
Used only for new stock, by the markdown watch: Burlington had one used unit (2026-10-16).
"""

import json
import re

import requests
from bs4 import BeautifulSoup

from ..domain.listing import NewUnit
from .kijiji_scraper import tracked_model

NEW_STOCK = "/search/inventory/availability/In%20Stock/usage/New"  # page 1; then /page/<n>
MAX_PAGES = 10  # Burlington had 4 (109 new units, 2026-10-16)
_TRADEMARKS = re.compile(r"[®™]")
# Burlington stalls the Windows Chrome agent in kijiji_scraper.USER_AGENTS (read timeout,
# 2026-10-16); a plain one gets the page in ~1s
HEADERS = {"User-Agent": "Mozilla/5.0"}


def parse_units(html: str) -> list[dict]:
    """The unit JSON of every card on a search page. [] if the cards changed shape."""
    units = []
    for span in BeautifulSoup(html, "html.parser").select("span.datasource"):
        try:
            unit = json.loads(span.get_text())
        except ValueError:
            continue
        if isinstance(unit, dict) and unit.get("productId"):
            units.append(unit)
    return units


def _https(url: str | None) -> str | None:
    return f"https:{url}" if url and url.startswith("//") else url


class AriNewStock:
    """A NewStockSource (ports.py): one dealer's new units of the tracked models."""

    def __init__(self, dealer: str, base_url: str) -> None:
        self.dealer = dealer
        self.base_url = base_url

    def __call__(self, slugs: list[str]) -> list[NewUnit]:
        units = []
        for d in _new_stock(self.base_url):
            name = " ".join(str(d.get(k) or "") for k in ("itemYear", "itemMake", "itemModel"))
            title = " ".join(_TRADEMARKS.sub("", name).split())
            slug = tracked_model(title, slugs)
            price, regular = float(d.get("itemPrice") or 0), float(d.get("unitPrice") or 0)
            if d.get("usageStatus") != "New" or not slug or not price:
                continue
            units.append(
                NewUnit(
                    dealer=self.dealer,
                    unit_id=str(d["productId"]),
                    model_slug=slug,
                    title=title,
                    price=price,
                    was=regular if d.get("itemOnSale") and regular > price else None,
                    url=_https(str(d.get("itemUrl") or "")) or self.base_url,
                    image_url=_https(d.get("itemThumbNailUrl")),
                )
            )
        return units


def _new_stock(base_url: str, timeout: int = 30) -> list[dict]:
    """Every new unit, page by page until a page adds none or is past the end (404).
    Raises if the site is down."""
    seen: dict[str, dict] = {}
    for n in range(1, MAX_PAGES + 1):
        url = base_url + NEW_STOCK + (f"/page/{n}" if n > 1 else "")
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        if response.status_code == 404 and seen:
            break
        response.raise_for_status()
        page = {str(u["productId"]): u for u in parse_units(response.text)}
        if not page.keys() - seen.keys():
            break
        seen |= page
    return list(seen.values())


BURLINGTON = AriNewStock("Burlington Cycle", "https://www.burlington-cycle.com")
