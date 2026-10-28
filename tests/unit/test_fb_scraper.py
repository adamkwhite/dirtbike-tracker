"""Facebook Marketplace parsing and throttling, on synthetic rendered HTML (no browser)."""

import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.application.ports import App
from dirtbike_tracker.infrastructure import fb_scraper
from dirtbike_tracker.infrastructure.fb_scraper import (
    FacebookSource,
    build_search_url,
    parse_listings,
    search_queries,
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


def test_drops_junk_that_kijiji_filters_would_catch() -> None:
    page = (
        _card("1", "CA$700", "2008 Loncin 125cc", "Mississauga, ON")  # not the model's family
        + _card("2", "CA$1,900", "2006 Honda crf70f", "Toronto, ON")  # a different model
        + _card("3", "CA$1,234", "WANTED Honda CRF100F Parts Bike", "Vaughan, ON")
        + _card("4", "CA$2,100", "2019 crf 100f", "Oakfield, NY")  # out of province
        + _card("5", "CA$1,234", "2023 Honda crf", "West Lincoln, ON")  # placeholder price
        + _card("6", "CA$3,500", "Just listed", "2008 crf100f", "Essa, ON")  # status badge
    )
    listings = {x.external_id: x for x in parse_listings(page, "crf100f")}

    assert sorted(listings) == ["5", "6"]
    assert (listings["5"].price, listings["5"].price_state) == (None, "unknown")
    assert (listings["6"].title, listings["6"].location) == ("2008 crf100f", "Essa, ON")


def test_drops_accessory_ads_but_keeps_bikes_that_mention_one() -> None:
    page = (
        _card("1", "CA$280", "Luggage racks for CB500X & NX500", "Hamilton, ON")
        + _card("2", "CA$125", "Honda CB500X / NX500 Saddlebag Supports", "London, ON")
        + _card("3", "CA$3,900", "2013 Honda CB500X new tires", "Ajax, ON")
        + _card("4", "CA$900", "Honda CB500X parts bike", "Barrie, ON")
    )

    assert sorted(x.external_id for x in parse_listings(page, "cb500x")) == ["3", "4"]


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


def test_searches_take_the_spaced_query_in_turn(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url.split("query=")[1])
        return PAGE

    source = FacebookSource(tmp_path / "fb.json", fetch=fetch, min_interval=timedelta(0))
    for _ in range(3):
        source("klx140", "ontario", 3)
    source("vulcans", "ontario", 3)  # no number to space out
    source("ttr125le", "ontario", 3, query="ttr125")  # a configured query is used as is

    assert fetched == ["klx140", "klx%20140", "klx140", "vulcans", "ttr125"]
    assert search_queries("crf150f") == ["crf150f", "crf 150f"]


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


class FakeBrowser:
    """Stands in for playwright.sync_api: records what the scraper asks the browser to do."""

    def __init__(self, html: str = PAGE) -> None:
        self.html = html
        self.calls: list[tuple] = []

    # sync_playwright() is a context manager yielding the playwright object
    def __call__(self) -> "FakeBrowser":
        return self

    def __enter__(self) -> "FakeBrowser":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    @property
    def chromium(self) -> "FakeBrowser":
        return self

    def launch_persistent_context(self, profile: str, **kwargs: object) -> "FakeBrowser":
        self.calls.append(("launch", profile, kwargs["headless"]))
        return self

    def new_page(self) -> "FakeBrowser":
        return self

    def goto(self, url: str, **_: object) -> None:
        self.calls.append(("goto", url))

    def wait_for_timeout(self, ms: int) -> None:
        self.calls.append(("wait", ms))

    def wait_for_event(self, event: str, timeout: int) -> None:
        self.calls.append(("wait_for", event, timeout))

    def content(self) -> str:
        return self.html

    def close(self) -> None:
        self.calls.append(("close",))


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> FakeBrowser:
    fake = FakeBrowser()
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return fake


def test_fetch_renders_headless_with_the_profile(browser: FakeBrowser, tmp_path: Path) -> None:
    html = fb_scraper.fetch_search_page("https://fb/search", profile_dir=tmp_path / "profile")

    assert html == PAGE
    assert browser.calls == [
        ("launch", str(tmp_path / "profile"), True),
        ("goto", "https://fb/search"),
        ("wait", fb_scraper.RENDER_WAIT_MS),
        ("close",),
    ]


def test_login_opens_a_visible_browser_until_closed(browser: FakeBrowser, tmp_path: Path) -> None:
    fb_scraper.login(profile_dir=tmp_path / "profile")

    assert browser.calls == [
        ("launch", str(tmp_path / "profile"), False),
        ("goto", "https://www.facebook.com/login"),
        ("wait_for", "close", 0),
        ("close",),
    ]


def test_fb_login_command(monkeypatch: pytest.MonkeyPatch, app: App) -> None:
    opened: list[bool] = []
    monkeypatch.setattr(fb_scraper, "login", lambda: opened.append(True))

    result = CliRunner().invoke(cli, ["fb-login"], obj=app)

    assert result.exit_code == 0, result.output
    assert opened == [True]
    assert "Saved." in result.output


def test_fb_login_without_playwright_says_how_to_install(
    monkeypatch: pytest.MonkeyPatch, app: App
) -> None:
    def missing() -> None:
        raise ImportError("No module named 'playwright'")

    monkeypatch.setattr(fb_scraper, "login", missing)

    result = CliRunner().invoke(cli, ["fb-login"], obj=app)

    assert result.exit_code == 1
    assert "pip install -e '.[fb]'" in result.output


@pytest.mark.parametrize(
    ("title", "kept"),
    [
        ("2005 klx 125L big wheel", False),  # a KLX125: a model we don't list, but not ours
        ("2009 Kawasaki klx", True),  # vague: no number, trusted to FB's search
        ("2017 klx 140g", True),
        ("Kawasaki KLX-140", True),
        ("Kawasaki KLX 140 G big wheel version", True),  # "wheel", but at a bike's price
        ("Kawasaki KLX 2010", True),  # a year after the family, not a model number
    ],
)
def test_vague_titles_must_not_name_another_variant(title: str, kept: bool) -> None:
    page = _card("1", "CA$2,500", title, "Barrie, ON")

    assert bool(parse_listings(page, "klx140")) is kept


def test_drops_cheap_kits() -> None:
    page = _card("1", "CA$90", "Honda NX500 / CB500X lowering kit", "London, ON")

    assert parse_listings(page, "nx500") == []


@pytest.mark.parametrize(
    ("price", "title", "kept"),
    [
        ("CA$60", "klx140 chain and sprocket kit", False),  # a part
        ("CA$280", "Luggage racks for KLX140", False),
        ("CA$700", "klx140 needs work, new tires", True),  # a project bike that has a part
        ("CA$450", "klx140 non runner, exhaust off", True),
    ],
)
def test_accessory_words_drop_parts_not_cheap_project_bikes(
    price: str, title: str, kept: bool
) -> None:
    assert bool(parse_listings(_card("1", price, title, "Barrie, ON"), "klx140")) is kept


@pytest.mark.parametrize(
    "title", ["2023 Honda CRF 125F", "2019 crf125fb big wheel", "Honda CRF125F-B"]
)
def test_crf125f_takes_both_wheel_sizes(title: str) -> None:
    (listing,) = parse_listings(_card("1", "CA$3,000", title, "Markham, ON"), "crf125f")

    assert listing.model_slugs == ["crf125f"]


@pytest.mark.parametrize(
    "title", ["2023 Kawasaki KLX 230 RS", "2021 klx230r s", "Kawasaki KLX-230S dual sport"]
)
def test_klx230_takes_every_variant(title: str) -> None:
    (listing,) = parse_listings(_card("1", "CA$4,500", title, "Barrie, ON"), "klx230")

    assert listing.model_slugs == ["klx230"]


def test_post_time_is_the_ads_not_a_seller_reviews() -> None:
    page = (
        '{"seller_review_snippets":[{"creation_time":1786492036,"review_text":"Bad attitude"}],'
        '"redacted_description":{"text":"Runs great \\"as is\\"\\nNew clutch"},'
        '"creation_time":1789149661}'
    )

    assert fb_scraper.parse_post_time(page) == "2026-09-11T18:01:01.000Z"
    assert fb_scraper.parse_post_time('{"creation_time":1786492036}') is None
    assert fb_scraper.ad_post_date("u", fetch=lambda _: page) == "2026-09-11T18:01:01.000Z"
