import json

from dirtbike_tracker.infrastructure.autotrader_scraper import build_search_url, parse_listings


def _ad(ad_id: str, model: str, offer_type: str, price: int | None, year: int = 2022) -> dict:
    return {
        "id": ad_id,
        "url": f"https://www.autotrader.ca/motorcycles-atvs/offers/x-{ad_id}",
        "price": {"priceRaw": price} if price else {},
        "location": {"city": "Oakville"},
        "description": "Runs great",
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
