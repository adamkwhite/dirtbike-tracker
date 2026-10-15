import json
from datetime import UTC, datetime

import pytest

from dirtbike_tracker.infrastructure import autotrader_scraper
from dirtbike_tracker.infrastructure.autotrader_scraper import (
    build_family_url,
    build_search_url,
    parse_listings,
    scrape_model,
)


def _ad(ad_id: str, model: str, offer_type: str, price: int | None, year: int = 2022) -> dict:
    return {
        "id": ad_id,
        "url": f"https://www.autotrader.ca/motorcycles-atvs/offers/x-{ad_id}",
        "price": {"priceRaw": price} if price else {},
        "location": {"city": "Oakville"},
        "description": "Runs great",
        "images": ["https://prod.pictures.autoscout24.net/listing-images/x.jpg/250x188.webp"],
        "vehicle": {
            "make": "Honda",
            "model": model,
            "modelYear": year,
            "offerType": offer_type,
            "mileageInKm": "25,538 km",
        },
    }


def _page(*ads: dict) -> str:
    payload = json.dumps({"props": {"pageProps": {"numberOfResults": len(ads), "listings": ads}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def test_parses_used_matching_listings_from_next_data() -> None:
    html = _page(
        _ad("used-1", "CRF250F", "U", 5499),
        _ad("new-1", "CRF250F", "N", 6999, year=2026),  # dealer stock
        _ad("other-1", "Gold Wing", "U", 19900),  # different model
        _ad("contact-1", "CRF250F", "U", None),
    )

    listings = parse_listings(html, "crf250f")

    assert [(x.external_id, x.title, x.price, x.price_state) for x in listings] == [
        ("used-1", "2022 Honda CRF250F", 5499.0, "numeric"),
        ("contact-1", "2022 Honda CRF250F", None, "unknown"),
    ]
    assert listings[0].location == "Oakville"
    assert listings[0].model_slugs == ["crf250f"]


def test_missing_or_malformed_page_data_returns_nothing() -> None:
    assert parse_listings("<html></html>", "crf250f") == []
    assert parse_listings('<script id="__NEXT_DATA__">{"props": {}}</script>', "crf250f") == []


def test_search_keyword_override() -> None:
    assert build_search_url("rebel500").endswith("kwd=rebel%20500")
    assert build_search_url("crf150f").endswith("kwd=crf150f")


def test_reads_year_and_km() -> None:
    (listing,) = parse_listings(_page(_ad("used-1", "CRF250F", "U", 5499)), "crf250f")

    assert (listing.year, listing.km) == (2022, 25538)
    assert (
        listing.image_url
        == "https://prod.pictures.autoscout24.net/listing-images/x.jpg/250x188.webp"
    )


@pytest.mark.parametrize(
    ("seller", "year_offset", "expected"),
    [("PrivateSeller", 0, ["private"]), ("Dealer", -2, ["dealer"]), ("Dealer", 0, [])],
)
def test_reads_seller_and_skips_dealer_current_year(
    seller: str, year_offset: int, expected: list[str]
) -> None:
    ad = _ad("used-1", "CRF250F", "U", 5499, year=datetime.now(UTC).year + year_offset)
    ad["seller"] = {"type": seller}

    assert [x.seller_type for x in parse_listings(_page(ad), "crf250f")] == expected


def _family_page(path: str, pages: int, *ads: dict) -> str:
    props = {"listings": list(ads), "numberOfPages": pages}
    props["pagePath"] = f"/motorcycles-atvs/lst/{path}/reg_on/ot_used?loc=Ontario"
    payload = json.dumps({"props": {"pageProps": props}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """AutoTrader by URL: tests fill it; every fetch is recorded in pages["_fetched"]."""
    served: dict[str, str] = {}
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url)
        return served.get(url, _page())

    autotrader_scraper._fetch_cached.cache_clear()
    monkeypatch.setattr(autotrader_scraper, "fetch_page", fetch)
    served["_fetched"] = fetched  # type: ignore[assignment]
    return served


def test_family_url() -> None:
    assert build_family_url("honda/crf", page=2) == (
        "https://www.autotrader.ca/motorcycles-atvs/lst/honda/crf/reg_on/ot_used?loc=Ontario&page=2"
    )


def test_adds_family_pages_to_the_keyword_search(pages: dict) -> None:
    pages[build_search_url("crf250f")] = _page(_ad("1", "CRF250F", "U", 5499))
    pages[build_family_url("honda/crf")] = _family_page(
        "honda/crf",
        2,
        _ad("1", "CRF250F", "U", 5499),  # also in the keyword results
        _ad("2", "CRF", "U", 4800) | {"vehicle": {"model": "CRF", "modelVersionInput": "250F"}},
        _ad("3", "CRF 300L", "U", 6500),  # another model in the family
    )
    pages[build_family_url("honda/crf", page=2)] = _family_page(
        "honda/crf", 2, _ad("4", "CRF 250F", "U", 3900)
    )

    assert sorted(x.external_id for x in scrape_model("crf250f")) == ["1", "2", "4"]
    scrape_model("crf150f")  # shares honda/crf: served from the per-run cache
    assert pages["_fetched"].count(build_family_url("honda/crf")) == 1


def test_rejects_a_make_page_served_for_an_unknown_family(pages: dict) -> None:
    make_page = _family_page("honda", 54, _ad("9", "CRF250F", "U", 5000))
    pages[build_family_url("honda/crf")] = make_page

    assert scrape_model("crf250f") == []
    assert pages["_fetched"].count(build_family_url("honda/crf", page=2)) == 0


def test_family_pages_stop_at_the_cap(pages: dict) -> None:
    for n in range(1, 6):
        pages[build_family_url("kawasaki/klx", page=n)] = _family_page("kawasaki/klx", 9)

    scrape_model("klx140")

    assert [u for u in pages["_fetched"] if "kawasaki/klx" in u] == [
        build_family_url("kawasaki/klx", page=n) for n in (1, 2, 3)
    ]
