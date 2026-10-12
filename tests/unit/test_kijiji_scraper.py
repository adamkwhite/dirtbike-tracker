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
