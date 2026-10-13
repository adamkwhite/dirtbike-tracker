"""Facebook Marketplace parsing and throttling, on synthetic rendered HTML (no browser)."""

from datetime import timedelta
from pathlib import Path

from dirtbike_tracker.infrastructure.fb_scraper import (
    FacebookSource,
    build_search_url,
    parse_listings,
)


def _card(item_id: str, *lines: str, img: str = "https://scontent.xx.fbcdn.net/a.jpg") -> str:
    spans = "".join(f"<span>{line}</span>" for line in lines)
    return f'<a href="/marketplace/item/{item_id}/?ref=search"><img src="{img}">{spans}</a>'


PAGE = (
    _card("111", "CA$2,450", "CA$2,950", "2003 Honda crf", "Caledon, ON")  # dropped from 2,950
    + _card("111", "CA$2,450", "2003 Honda crf", "Caledon, ON")  # FB repeats cards
    + _card("222", "CA$3,600", "2017 Honda crf150f", "Oakville, ON", "12K km")
    + _card("333", "CA$2,800", "CRF150R", "Hamilton, ON")  # a different model
    + _card("444", "Free", "2008 Honda CRF150F parts bike", "Erin, ON")
)


def test_parses_cards_keeping_vague_titles_and_dropping_other_models() -> None:
    listings = {x.external_id: x for x in parse_listings(PAGE, "crf150f")}

    assert sorted(listings) == ["111", "222", "444"]
    vague = listings["111"]
    assert (vague.price, vague.title, vague.location, vague.year) == (
        2450.0,
        "2003 Honda crf",
        "Caledon, ON",
        2003,
    )
    assert vague.url == "https://www.facebook.com/marketplace/item/111/"
    assert vague.image_url == "https://scontent.xx.fbcdn.net/a.jpg"
    assert (listings["222"].km, listings["222"].model_slugs) == (12000, ["crf150f"])
    assert (listings["444"].price, listings["444"].price_state) == (None, "unknown")


def test_search_url() -> None:
    assert build_search_url("rebel 500", city="toronto") == (
        "https://www.facebook.com/marketplace/toronto/search/?query=rebel%20500"
    )


def test_source_searches_each_model_at_most_once_per_interval(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url)
        return PAGE

    source = FacebookSource(tmp_path / "fb.json", fetch=fetch, min_interval=timedelta(hours=6))

    first = source("crf150f", "ontario", 3)
    again = source("crf150f", "ontario", 3)  # within 6h: skipped
    other = source("crf230f", "ontario", 3)  # another model has its own clock

    assert (len(first), again, len(fetched)) == (3, [], 2)
    # a vague title is trusted to FB's search: kept for whichever model searched it
    assert [x.external_id for x in other] == ["111"]
    assert FacebookSource(tmp_path / "fb.json", fetch=fetch, min_interval=timedelta(0))(
        "crf150f", "ontario", 3
    )


def test_failed_search_returns_nothing_and_retries_next_run(tmp_path: Path) -> None:
    calls: list[str] = []

    def broken(url: str) -> str:
        calls.append(url)
        raise TimeoutError("page never rendered")

    source = FacebookSource(tmp_path / "fb.json", fetch=broken)

    assert source("crf150f", "ontario", 3) == []
    assert source("crf150f", "ontario", 3) == []  # not throttled: the failure wasn't recorded
    assert len(calls) == 2


def test_missing_playwright_is_a_skip_not_a_crash(tmp_path: Path) -> None:
    def no_playwright(url: str) -> str:
        raise ImportError("No module named 'playwright'")

    assert FacebookSource(tmp_path / "fb.json", fetch=no_playwright)("crf150f", "ontario", 3) == []
