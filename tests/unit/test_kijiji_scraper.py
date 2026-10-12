import json

from dirtbike_tracker.infrastructure.kijiji_scraper import parse_listings


def _card(ad_id: str, title: str) -> str:
    return (
        '<div data-testid="listing-card">'
        f'<a href="/v-dirt-bikes-motocross/ontario/x/{ad_id}"><h3>{title}</h3></a>'
        '<p data-testid="listing-price">$3,000.00</p>'
        "</div>"
    )


def _next_data(*ads: tuple[str, str]) -> str:
    listings = [
        {
            "id": ad_id,
            "attributes": {
                "all": [{"canonicalName": "vehicletype", "canonicalValues": [vehicle_type]}]
            },
        }
        for ad_id, vehicle_type in ads
    ]
    payload = json.dumps({"props": {"pageProps": {"listings": listings}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def test_parse_listings_skips_new_vehicle_ads() -> None:
    html = (
        _card("1743029355", "2027 Kawasaki KLX140R")
        + _card("1700000001", "2008 Kawasaki KLX140")
        + _next_data(("1743029355", "new"), ("1700000001", "used"))
    )

    listings = parse_listings(html, "klx140")

    assert [listing.external_id for listing in listings] == ["1700000001"]


def test_parse_listings_keeps_everything_without_page_data() -> None:
    html = (
        _card("1743029355", "2027 Kawasaki KLX140R") + '<script id="__NEXT_DATA__">{oops</script>'
    )

    assert len(parse_listings(html, "klx140")) == 1


def test_parse_listings_uses_activation_date_as_posted_text() -> None:
    page_data = json.dumps(
        {"props": {"ads": [{"id": "1700000001", "activationDate": "2026-09-01T10:00:00.000Z"}]}}
    )
    html = (
        _card("1700000001", "2008 Kawasaki KLX140")
        + f'<script id="__NEXT_DATA__" type="application/json">{page_data}</script>'
    )

    (listing,) = parse_listings(html, "klx140")

    assert listing.posted_text == "2026-09-01T10:00:00.000Z"


def test_parse_listings_filters_by_model_in_title() -> None:
    html = (
        _card("1700000001", "Honda CRF150F")
        + _card("1700000002", "Yamaha TTR125 + CRF150F package")
        + _card("1700000003", "Yamaha TTR125")
    )

    listings = parse_listings(html, "crf150f")

    assert [listing.model_slugs for listing in listings] == [["crf150f"], ["crf150f", "ttr125"]]


def test_parse_listings_loose_match_ignores_separators() -> None:
    (known,) = parse_listings(_card("1700000001", "Honda XR-100"), "xr 100")
    (unknown,) = parse_listings(_card("1700000002", "Zongshen ZZ-9"), "zz9")

    assert known.model_slugs == ["xr100"]
    assert unknown.model_slugs == ["zz9"]


def test_parse_listings_skips_cards_without_link_or_outside_category() -> None:
    html = (
        '<div data-testid="listing-card"><h3>Honda CRF150F</h3></div>'
        '<div data-testid="listing-card"><a href="/v-cars/ontario/x/1700000009">CRF150F</a></div>'
    )

    assert parse_listings(html, "crf150f") == []
