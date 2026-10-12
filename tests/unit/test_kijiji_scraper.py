import json

import pytest
import requests

from dirtbike_tracker.infrastructure import kijiji_scraper
from dirtbike_tracker.infrastructure.kijiji_scraper import parse_ad_description, parse_listings


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


def _ad_page(cache: object) -> str:
    payload = json.dumps({"props": {"pageProps": {"__APOLLO_STATE__": cache}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def test_parse_ad_description_reads_apollo_cache() -> None:
    html = _ad_page(
        {
            "StandardListing:1744639032": {"description": "Both bikes run well."},
            "StandardListing:1700000001": {"description": "someone else's ad"},
        }
    )

    assert parse_ad_description(html, "1744639032") == "Both bikes run well."


@pytest.mark.parametrize(
    "html",
    [
        "<html>no page data</html>",
        '<script id="__NEXT_DATA__">{oops</script>',
        _ad_page({"StandardListing:1744639032": {"title": "no description"}}),
        _ad_page({"StandardListing:1700000001": {"description": "other ad"}}),
    ],
)
def test_parse_ad_description_none_when_missing(html: str) -> None:
    assert parse_ad_description(html, "1744639032") is None


def test_fetch_ad_description_by_url_id(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _ad_page({"StandardListing:1744639032": {"description": "Sat for 3 years."}})
    monkeypatch.setattr(kijiji_scraper, "fetch_page", lambda url: page)

    url = "https://www.kijiji.ca/v-dirt-bikes-motocross/barrie/crf/1744639032"
    assert kijiji_scraper.fetch_ad_description(url) == "Sat for 3 years."


def test_fetch_ad_description_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(url: str) -> str:
        raise requests.HTTPError("404")

    monkeypatch.setattr(kijiji_scraper, "fetch_page", gone)

    assert kijiji_scraper.fetch_ad_description("https://www.kijiji.ca/v-x/1744639032") is None


def _page_with_snippet(description: str) -> str:
    page_data = json.dumps({"props": {"ads": [{"id": "1700000001", "description": description}]}})
    return (
        '<div data-testid="listing-card"><a href="/v-dirt-bikes-motocross/ontario/x/1700000001">'
        '<h3>2008 Honda CRF150F</h3></a><p data-testid="listing-description">card text</p></div>'
        f'<script id="__NEXT_DATA__" type="application/json">{page_data}</script>'
    )


def test_parse_listings_prefers_search_page_description_over_card() -> None:
    (listing,) = parse_listings(_page_with_snippet("Sat for 3 years, needs carb."), "crf150f")

    assert listing.description == "Sat for 3 years, needs carb."
    assert listing.full_description == "Sat for 3 years, needs carb."  # short = the whole ad


def test_parse_listings_cut_off_snippet_is_not_the_full_ad() -> None:
    (listing,) = parse_listings(_page_with_snippet("Runs great, electric start, ..."), "crf150f")

    assert listing.description == "Runs great, electric start, ..."
    assert listing.full_description is None


def test_parse_listings_falls_back_to_card_description() -> None:
    (listing,) = parse_listings(_page_with_snippet("   "), "crf150f")

    assert (listing.description, listing.full_description) == ("card text", None)


def _attrs_page(**attrs: str) -> str:
    fields = [{"canonicalName": k, "canonicalValues": [v]} for k, v in attrs.items()]
    page_data = json.dumps(
        {"props": {"ads": [{"id": "1700000001", "attributes": {"all": fields}}]}}
    )
    return _card("1700000001", "2018 Honda CB500X") + (
        f'<script id="__NEXT_DATA__" type="application/json">{page_data}</script>'
    )


def test_parse_listings_reads_year_and_km_fields() -> None:
    (listing,) = parse_listings(_attrs_page(caryear="2018", carmileageinkms="16672"), "cb500x")
    (blank,) = parse_listings(_attrs_page(caryear="2018", carmileageinkms="0"), "cb500x")

    assert (listing.year, listing.km) == (2018, 16672)
    assert (blank.year, blank.km) == (2018, None)  # 0 = seller skipped the field
