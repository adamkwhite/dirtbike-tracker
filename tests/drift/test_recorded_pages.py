"""Our parsers against real recorded pages: catches site drift (#28).

The pages live in tests/recorded/ (gitignored: third-party ads with personal
info, and this repo is public), so CI skips this file. Locally:
`make drift` re-records from the live sites and runs it. A failure means
Kijiji or AutoTrader changed something a parser depends on.
"""

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from dirtbike_tracker.domain.listing import Listing
from dirtbike_tracker.infrastructure import autotrader_scraper, kijiji_scraper

RECORDED = Path(__file__).resolve().parent.parent / "recorded"
if not (RECORDED / "manifest.json").exists():
    pytest.skip("no recorded pages: run `make drift`", allow_module_level=True)

PAGES = json.loads((RECORDED / "manifest.json").read_text())["pages"]
PRICE_STATES = {"numeric", "please_contact", "swap_trade", "unknown"}


def _html(name: str) -> str:
    return (RECORDED / name).read_text()


def _assert_required_fields(listings: list[Listing], url_prefix: str) -> None:
    assert listings, "parsed no listings"
    for x in listings:
        assert x.external_id, x
        assert x.title, x
        assert x.url.startswith(url_prefix), x.url
        assert x.price_state in PRICE_STATES, x
        assert x.price is not None or x.price_state != "numeric", x
    assert any(x.price for x in listings), "no listing has a price"
    assert any(x.image_url for x in listings), "no photos (#10)"


KIJIJI_SEARCHES = sorted(n for n in PAGES if n.startswith("kijiji_search_"))


@pytest.mark.parametrize("name", KIJIJI_SEARCHES)
def test_kijiji_search_parses(name: str) -> None:
    listings = kijiji_scraper.parse_listings(_html(name), PAGES[name]["slug"])

    _assert_required_fields(listings, "https://www.kijiji.ca/v-")
    assert all(re.fullmatch(r"\d{10,}", x.external_id) for x in listings)
    assert any(x.description for x in listings), "no search-page snippets (#76)"
    assert any(x.posted_text for x in listings), "no activationDate"


def test_kijiji_street_bikes_have_year_and_km() -> None:
    listings = kijiji_scraper.parse_listings(_html("kijiji_search_rebel500.html"), "rebel500")

    assert any(x.year for x in listings), "caryear stopped parsing"
    assert any(x.km for x in listings), "carmileageinkms stopped parsing"


@pytest.mark.parametrize("name", KIJIJI_SEARCHES)
def test_kijiji_new_stock_is_skipped(name: str) -> None:
    html = _html(name)
    meta = kijiji_scraper._ad_meta(BeautifulSoup(html, "html.parser"))
    new_ids = {ad_id for ad_id, facts in meta.items() if facts.get("new")}

    parsed = {x.external_id for x in kijiji_scraper.parse_listings(html, PAGES[name]["slug"])}

    assert not parsed & new_ids


@pytest.mark.skipif("kijiji_ad.html" not in PAGES, reason="no ad page recorded")
def test_kijiji_ad_page_has_the_description() -> None:
    text = kijiji_scraper.parse_ad_description(
        _html("kijiji_ad.html"), PAGES["kijiji_ad.html"]["external_id"]
    )

    assert text, "__APOLLO_STATE__ description stopped parsing (#71)"


def test_autotrader_search_parses_and_skips_new_stock() -> None:
    html = _html("autotrader_search.html")
    script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    raw = json.loads(script.get_text())["props"]["pageProps"]["listings"]  # type: ignore[union-attr]
    new_ids = {str(ad["id"]) for ad in raw if (ad.get("vehicle") or {}).get("offerType") == "N"}

    listings = autotrader_scraper.parse_listings(html, PAGES["autotrader_search.html"]["slug"])

    _assert_required_fields(listings, "https://www.autotrader.ca/")
    assert not {x.external_id for x in listings} & new_ids
    assert any(x.year for x in listings), "modelYear stopped parsing"
    assert any(x.km for x in listings), "mileageInKm stopped parsing"
