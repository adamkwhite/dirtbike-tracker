"""A second watchlist (config/friend.yml): own config, own alerts, new stock, year/trim filters."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.application.ports import App
from dirtbike_tracker.domain.listing import Listing
from dirtbike_tracker.domain.scoring import matches_spec
from dirtbike_tracker.infrastructure import autotrader_scraper, kijiji_scraper, storage

from .conftest import PushSpy

SPEED_TWIN = {
    "slug": "speedtwin",
    "query": "speed twin 1200",
    "goal": "ride",
    "include_new": True,
    "min_year": 2025,
    "require": ["RS"],
    "alert": "all",
}


def _bike(ext_id: str, title: str, price: float, year: int | None) -> Listing:
    return Listing(
        source="kijiji",
        external_id=ext_id,
        url=f"https://www.kijiji.ca/v-sport-bikes/oshawa/x/{ext_id}",
        title=title,
        price=price,
        price_state="numeric",
        location="Oshawa",
        posted_text="",
        description="Speed Triple 1200 RS and Trident 660 also in stock",  # dealer boilerplate
        model_slugs=["speedtwin"],
        scraped_at="",
        year=year,
        seller_type="dealer",
    )


@pytest.mark.parametrize(
    ("title", "year", "wanted"),
    [
        ("2026 Triumph Speed Twin 1200 RS Sapphire Black", 2026, True),
        ("Triumph Speed Twin 1200RS", None, True),  # unknown year passes
        ("2024 Triumph Speed Twin 1200 RS", 2024, False),  # too old
        ("2026 Triumph Speed Twin 1200", 2026, False),  # base model; RS only in the description
        ("2026 Aprilia RSV4", 2026, False),  # "RSV4" doesn't name RS
    ],
)
def test_matches_spec(title: str, year: int | None, wanted: bool) -> None:
    assert matches_spec(_bike("1", title, 19995, year), 2025, ["RS"]) is wanted


def test_watchlist_alerts_every_match_once_then_on_a_drop(app: App, sent: PushSpy) -> None:
    calls: list[dict] = []
    stock = [
        _bike("1", "2026 Triumph Speed Twin 1200 RS Sapphire Black", 19995, 2026),
        _bike("2", "2026 Triumph Speed Twin 1200", 15995, 2026),
        _bike("3", "2022 Triumph Speed Twin 1200 RS", 13000, 2022),
    ]

    def kijiji(slug: str, region: str, pages: int, **opts: object) -> list[Listing]:
        calls.append(opts)
        return list(stock)

    app = replace(app, sources={"kijiji": kijiji}, config={"triumph": [SPEED_TWIN]})
    for _ in range(2):  # the second run finds nothing new
        assert CliRunner().invoke(cli, ["watch"], obj=app).exit_code == 0

    assert calls[0] == {"query": "speed twin 1200", "include_new": True}
    assert [p["title"] for p in sent] == ["New: SPEEDTWIN $19995"]
    assert "(dealer)" in sent[0]["body"]

    stock[0] = replace(stock[0], price=18495)
    CliRunner().invoke(cli, ["watch"], obj=app)
    assert sent[-1]["title"] == "PRICE DROP: SPEEDTWIN $18495"


def test_kijiji_keeps_new_stock_and_sport_bikes_when_asked() -> None:
    card = (
        '<div data-testid="listing-card"><a href="/v-sport-bikes/oshawa/x/1700000001">'
        "<h3>2026 Triumph Speed Twin 1200 RS</h3></a></div>"
    )
    attrs = [{"canonicalName": "vehicletype", "canonicalValues": ["new"]}]
    payload = {
        "props": {"pageProps": {"listings": [{"id": "1700000001", "attributes": {"all": attrs}}]}}
    }
    html = (
        card + f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
    )

    assert kijiji_scraper.parse_listings(html, "speedtwin") == []
    assert [
        x.title for x in kijiji_scraper.parse_listings(html, "speedtwin", include_new=True)
    ] == ["2026 Triumph Speed Twin 1200 RS"]


def test_autotrader_query_new_stock_and_spaced_titles() -> None:
    ad = {
        "id": "a1",
        "url": "https://www.autotrader.ca/motorcycles-atvs/offers/a1",
        "price": {"priceRaw": 24195},
        "vehicle": {
            "make": "Triumph",
            "model": "Speed Triple 1200 RS",
            "modelYear": 2026,
            "offerType": "N",  # new stock
        },
        "seller": {"type": "Dealer"},
    }
    payload = json.dumps({"props": {"pageProps": {"listings": [ad]}}})
    html = f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'

    assert autotrader_scraper.parse_listings(html, "speedtriple") == []
    kept = autotrader_scraper.parse_listings(html, "speedtriple", include_new=True)
    assert [(x.title, x.model_slugs) for x in kept] == [
        ("2026 Triumph Speed Triple 1200 RS", ["speedtriple"])
    ]
    assert "kwd=speed%20triple%201200" in autotrader_scraper.build_search_url(
        "speedtriple", query="speed triple 1200"
    )


def test_config_option_keeps_its_own_state_and_dashboard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "listings.db")
    monkeypatch.setattr(cli_module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cli_module, "ROOT", tmp_path)
    friend = tmp_path / "friend.yml"
    friend.write_text("triumph:\n  - slug: speedtwin\n")

    app = cli_module.build_app(friend)

    assert app.config == {"triumph": [{"slug": "speedtwin"}]}
    assert (app.state_dir, app.dashboard_path) == (
        tmp_path / "state" / "friend",
        tmp_path / "dashboard-friend.html",
    )


@pytest.mark.parametrize(
    ("title", "wanted"),
    [
        ("2012 Kawasaki KLX110L", True),
        ("2025 Kawasaki KLX110R L", True),
        ("klx110 rl (best offer or trade)", True),  # RL: the L after a letter
        ("Kawasaki klx 110", False),  # base model
        ("2023klx110r", False),
    ],
)
def test_matches_spec_require_alternatives(title: str, wanted: bool) -> None:
    assert matches_spec(_bike("1", title, 2500, 2021), 2010, ["L|RL"]) is wanted


@pytest.mark.parametrize(
    ("title", "year", "wanted"),
    [
        ("2003 Honda crf", 2003, False),  # before the CRF250F (2019+)
        ("2021 Honda CRF250F", 2021, True),
        ("2023 Honda crf", 2023, False),  # after the CRF230F (to 2019), with a max_year
        ("Honda CRF250F", None, True),  # unknown year passes
    ],
)
def test_matches_spec_production_years(title: str, year: int | None, wanted: bool) -> None:
    min_year, max_year = (2003, 2019) if "2023" in title else (2019, None)

    assert matches_spec(_bike("1", title, 2500, year), min_year, max_year=max_year) is wanted


def test_a_title_naming_two_models_is_tagged_only_where_it_fits_the_spec(app: App) -> None:
    ad = replace(
        _bike("1", "2021 Kawasaki KLX 110 KLX300 D, STREET LEGAL", 3995, 2021),
        model_slugs=["klx110", "klx300", "kx65"],
    )
    config = {
        "secondary": [{"slug": "klx110", "min_year": 2010, "require": ["L|RL"]}],
        "dualsport": [{"slug": "klx300", "min_year": 1997}],
    }
    app = replace(app, sources={"kijiji": lambda *_, **__: [ad]}, config=config)

    assert CliRunner().invoke(cli, ["watch", "--group", "dualsport"], obj=app).exit_code == 0

    assert [x["external_id"] for x in app.store.active_listings("klx300")] == ["1"]
    assert app.store.active_listings("klx110") == []  # no L: not a KLX110L
    assert len(app.store.active_listings("kx65")) == 1  # an untracked model keeps its tag
