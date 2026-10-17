"""EquipmentSearch dealer sites, e.g. Cycle World: card parsing, paging and the source."""

import pytest
import requests

from dirtbike_tracker.infrastructure import escsite
from dirtbike_tracker.infrastructure.escsite import EscSource, page_count, parse_cards


def card(
    adid: str, year: str, make: str, model: str, price: str = "$5,498 CAD", km: str = "11288"
) -> str:
    """One inventory card, shaped like the live site's (2026-10-16)."""
    return f"""
    <div class="item col-xs-6 col-sm-3 list-group-item">
      <img loading="lazy" src="https://equipmentsearch.com/uploadedimages/8768/{adid}_400x0.jpg">
      <h2 class="model-title"><a class="model-title-link pushstate"
         href="/Pre-Owned/adid/{adid}/{year}-{make}-ADOPTED-BY-A-ROAD-WARRIOR/">{year} {make} ADOPTED BY ...</a></h2>
      <img loading="lazy" src="https://equipmentsearch.com/uploadedimages/manufacturers-logos/1495.png">
      <div class="price price show-item">Price: <span class="price-val">{price}</span>
        <span class="price-val hidden">($0 USD)</span></div>
      <div class="spec-col year"><span class="specs-title">Year</span> {year}</div>
      <div class="spec-col make"><span class="specs-title">Make</span> {make}</div>
      <div class="spec-col model"><span class="specs-title">Model</span> {model}</div>
      <div class="spec-col odometer"><span class="specs-title">Km</span> {km} km</div>
    </div>"""


def page(*cards: str, pages: int = 1) -> str:
    return f"<html><body>{''.join(cards)}<p>Show 20 Page 1 of {pages}</p></body></html>"


SOURCE = EscSource("cycleworld", "https://dealer.example", "Scarborough, ON")


def test_parses_a_card_into_a_listing() -> None:
    units = parse_cards(page(card("67681179", "2015", "Kawasaki", "VULCAN S")))

    (listing,) = SOURCE.parse(units, "vulcans")

    assert (listing.title, listing.price, listing.year, listing.km) == (
        "2015 Kawasaki VULCAN S",  # from the spec fields, not the marketing title
        5498.0,
        2015,
        11288,
    )
    assert (
        listing.url
        == "https://dealer.example/Pre-Owned/adid/67681179/2015-Kawasaki-ADOPTED-BY-A-ROAD-WARRIOR/"
    )
    assert listing.image_url == "https://equipmentsearch.com/uploadedimages/8768/67681179_400x0.jpg"
    assert (listing.seller_type, listing.location, listing.source) == (
        "dealer",
        "Scarborough, ON",
        "cycleworld",
    )


def test_a_model_field_that_repeats_the_year_is_the_whole_title() -> None:
    units = parse_cards(page(card("1", "2008", "Suzuki", "2008 Suzuki C50T Boulevard")))

    assert units[0]["title"] == "2008 Suzuki C50T Boulevard"


def test_no_price_and_other_models_are_skipped_or_unknown() -> None:
    units = parse_cards(
        page(
            card("1", "2015", "Kawasaki", "VULCAN S", price="$0 CAD"),
            card("2", "2014", "Ducati", "Monster 796"),
        )
    )

    (listing,) = SOURCE.parse(units, "vulcans")

    assert (listing.external_id, listing.price, listing.price_state) == ("1", None, "unknown")


def test_page_count_is_capped() -> None:
    assert page_count(page(pages=3)) == 3
    assert page_count(page(pages=99)) == escsite.MAX_PAGES
    assert page_count("<html></html>") == 1


@pytest.mark.parametrize("html", ["", "<div class='item'>no link</div>"])
def test_a_reshaped_page_parses_to_nothing(html: str) -> None:
    assert parse_cards(html) == []


def test_one_crawl_of_every_page_serves_every_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []
    pages = {
        "https://dealer.example/Pre-Owned/": page(
            card("1", "2015", "Kawasaki", "VULCAN S"), pages=2
        ),
        "https://dealer.example/Pre-Owned/page/2/": page(
            card("2", "2016", "Yamaha", "MT-07"), pages=2
        ),
    }

    def get(url: str, **_: object) -> object:
        fetched.append(url)
        return type("R", (), {"text": pages[url], "raise_for_status": lambda self: None})()

    escsite._inventory.cache_clear()
    monkeypatch.setattr(requests, "get", get)
    source = EscSource("cycleworld", "https://dealer.example", "Scarborough, ON")

    assert [x.external_id for x in source("vulcans", "on", 3)] == ["1"]
    assert [x.external_id for x in source("mt07", "on", 3)] == ["2"]
    assert source("crf150f", "on", 3) == []
    assert (len(fetched), source.last_fetch_ok) == (2, True)


def test_fetch_failure_returns_nothing_and_marks_the_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(url: str, **_: object) -> object:
        raise requests.ConnectionError("dealer site down")

    escsite._inventory.cache_clear()
    monkeypatch.setattr(requests, "get", down)
    source = EscSource("cycleworld", "https://dealer.example", "Scarborough, ON")

    assert source("vulcans", "on", 3) == []
    assert source.last_fetch_ok is False
