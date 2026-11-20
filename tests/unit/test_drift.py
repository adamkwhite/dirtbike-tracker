"""The drift checks report breakage, on small synthetic recordings (no network)."""

import json
from pathlib import Path

import pytest
import requests
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.application.ports import App
from dirtbike_tracker.infrastructure.drift import problems, run

from .conftest import PushSpy
from .test_escsite import card as esc_card
from .test_escsite import page as esc_page
from .test_focus import doc as focus_doc
from .test_shoparide import FakeSite, unit

SHOPARIDE = FakeSite([unit("a", "2023 Honda CRF250F")], [unit("e", "2021 Suzuki SV650")])
DEALERS = cli_module.build_dealers(cli_module.load_dealers())  # models.yml's real definitions


def _kijiji_search(fields: list[dict], title: str = "2019 Honda Rebel 500") -> str:
    """One card plus __NEXT_DATA__ carrying the given form fields."""
    card = (
        '<div data-testid="listing-card">'
        f'<a href="/v-street-cruisers-choppers/barrie/bike/1700000001"><h3>{title}</h3></a>'
        '<p data-testid="listing-price">$5,500.00</p></div>'
    )
    ad = {
        "id": "1700000001",
        "description": "Runs great",
        "activationDate": "2026-10-10T12:00:00.000Z",
        "imageUrls": ["https://media.kijiji.ca/a.jpg"],
        "attributes": {"all": fields},
    }
    payload = json.dumps({"props": {"pageProps": {"ads": [ad]}}})
    return f'{card}<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def _record(out: Path, html: str) -> Path:
    out.mkdir()
    (out / "kijiji_search_rebel500.html").write_text(html)
    pages = {"kijiji_search_rebel500.html": {"url": "u", "slug": "rebel500"}}
    (out / "manifest.json").write_text(json.dumps({"pages": pages}))
    return out


YEAR = {"canonicalName": "caryear", "canonicalValues": ["2019"]}
KM = {"canonicalName": "carmileageinkms", "canonicalValues": ["12000"]}
OWNER = {"canonicalName": "forsaleby", "canonicalValues": ["ownr"]}


def test_clean_recording_has_no_problems(tmp_path: Path) -> None:
    assert problems(_record(tmp_path / "rec", _kijiji_search([YEAR, KM, OWNER]))) == []


def test_reports_a_field_that_stopped_parsing(tmp_path: Path) -> None:
    out = _record(tmp_path / "rec", _kijiji_search([YEAR, OWNER]))  # km field renamed / gone

    assert problems(out) == ["kijiji_search_rebel500.html: carmileageinkms stopped parsing"]


def test_reports_a_page_that_parses_nothing(tmp_path: Path) -> None:
    out = _record(tmp_path / "rec", "<html>new layout</html>")

    assert problems(out) == ["kijiji_search_rebel500.html: parsed no listings"]


def test_drift_command_alerts_and_fails_on_problems(
    app: App, sent: PushSpy, tmp_path: Path
) -> None:
    out = _record(tmp_path / "rec", "<html>new layout</html>")

    result = CliRunner().invoke(cli, ["drift", "--replay", "--dir", str(out)], obj=app)

    assert result.exit_code == 1
    assert "parsed no listings" in result.output
    assert [p["title"] for p in sent] == ["Site drift: 1 parser problem(s)"]


def test_drift_command_ok_is_quiet(app: App, sent: PushSpy, tmp_path: Path) -> None:
    out = _record(tmp_path / "rec", _kijiji_search([YEAR, KM, OWNER]))

    result = CliRunner().invoke(cli, ["drift", "--replay", "--dir", str(out)], obj=app)

    assert result.exit_code == 0, result.output
    assert sent == []


def _ad_page(ad_id: str, body: str | None) -> str:
    entry = {"description": body} if body else {"title": "no description"}
    payload = json.dumps(
        {"props": {"pageProps": {"__APOLLO_STATE__": {f"StandardListing:{ad_id}": entry}}}}
    )
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def _autotrader_page(offer_types: list[str]) -> str:
    listings = [
        {
            "id": f"at-{i}",
            "url": f"https://www.autotrader.ca/motorcycles-atvs/offers/honda-rebel-500-at-{i}",
            "price": {"priceRaw": 5500},
            "location": {"city": "Oakville"},
            "images": ["https://prod.pictures.autoscout24.net/x.jpg/250x188.webp"],
            "seller": {"type": "PrivateSeller"},
            "vehicle": {
                "make": "Honda", "model": "Rebel 500", "modelYear": 2021,
                "offerType": offer, "mileageInKm": "12,000 km",
            },
        }
        for i, offer in enumerate(offer_types)
    ]  # fmt: skip
    payload = json.dumps({"props": {"pageProps": {"listings": listings}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


class FakeSites:
    """requests.get for record(): canned search pages, an ad page, AutoTrader."""

    def __init__(self, ad_body: str | None = "Runs great, safetied.") -> None:
        self.ad_body = ad_body

    def get(self, url: str, **_: object) -> object:
        if "shoparide.ca" in url:
            return SHOPARIDE.get(url)
        if "delmastro.ca" in url:
            text = "apiKey: 'k3y'"
        elif "cycleworldsuperstore.com" in url:
            text = esc_page(esc_card("67681179", "2015", "Kawasaki", "VULCAN S"))
        elif "sturgesscycle.com" in url:
            text = (
                "\ufeffvar V6SpbFeeDataVersion=2;\nvar Vehicles="
                + json.dumps([STURGESS_UNIT])
                + ";"
            )
        elif "autotrader.ca" in url:
            text = _autotrader_page(["U", "N"])
        elif "kijiji.ca/b-" in url:
            title = "2008 Honda CRF150F" if "crf150f" in url else "2019 Honda Rebel 500"
            text = _kijiji_search([YEAR, KM, OWNER], title)
        else:
            text = _ad_page(url.rsplit("/", 1)[-1], self.ad_body)
        attrs = {"text": text, "content": text.encode(), "raise_for_status": lambda self: None}
        return type("R", (), attrs)()

    def post(self, url: str, **_: object) -> object:
        """Del Mastro's Typesense search."""
        hits = [{"document": focus_doc("751149", "2018 HONDA REBEL 500")}]
        attrs = {
            "json": lambda self: {"results": [{"hits": hits}]},
            "raise_for_status": lambda self: None,
        }
        return type("R", (), attrs)()


STURGESS_UNIT = {
    "id": "18938900",
    "bike_year": "2025",
    "manuf": "Honda®",
    "model": "Rebel 500 ABS",
    "price": "7999",
    "type": "U",
    "miles": "1200",
    "bike_image": "AB6E156B-8E0B-419C-B26C-AA79829A3C28.jpg",
}


def _fake_web(monkeypatch: pytest.MonkeyPatch, sites: FakeSites) -> None:
    monkeypatch.setattr(requests, "get", sites.get)
    monkeypatch.setattr(requests, "post", sites.post)
    monkeypatch.setattr("time.sleep", lambda _: None)


def test_record_then_check_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_web(monkeypatch, FakeSites())

    assert run(tmp_path / "rec", sources=DEALERS) == []
    manifest = json.loads((tmp_path / "rec" / "manifest.json").read_text())
    assert sorted(manifest["pages"]) == [
        "autotrader_search.html",
        "cycleworld_preowned.html",
        "delmastro_inventory.json",
        "kijiji_ad.html",
        "kijiji_search_crf150f.html",
        "kijiji_search_rebel500.html",
        "shoparide_units.json",
        "sturgess_inventory.js",
    ]


def test_reports_a_reshaped_dealer_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_web(monkeypatch, FakeSites())
    out = tmp_path / "rec"
    run(out, sources=DEALERS)
    (out / "sturgess_inventory.js").write_text("var Inventory={};")

    assert run(out, replay=True) == ["sturgess_inventory.js: no `var Vehicles=[...]` units parsed"]


def test_reports_ad_page_description_gone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_web(monkeypatch, FakeSites(ad_body=None))

    assert run(tmp_path / "rec", sources=DEALERS) == [
        "kijiji_ad.html: __APOLLO_STATE__ description stopped parsing (#71)"
    ]


def test_reports_autotrader_page_data_gone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_web(monkeypatch, FakeSites())
    out = tmp_path / "rec"
    run(out, sources=DEALERS)
    (out / "autotrader_search.html").write_text("<html>redesign</html>")

    assert run(out, replay=True) == ["autotrader_search.html: __NEXT_DATA__ listings not found"]


def test_failed_fetch_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def down(url: str, **_: object) -> object:
        raise requests.ConnectionError("kijiji is down")

    monkeypatch.setattr(requests, "get", down)
    monkeypatch.setattr("time.sleep", lambda _: None)

    assert run(tmp_path / "rec", sources=DEALERS) == ["recording failed: kijiji is down"]


def test_missing_recordings_are_a_problem(tmp_path: Path) -> None:
    assert problems(tmp_path) == [f"no recordings in {tmp_path}"]


def test_reports_a_focus_index_with_no_used_units(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_web(monkeypatch, FakeSites())
    out = tmp_path / "rec"
    run(out, sources=DEALERS)
    (out / "delmastro_inventory.json").write_text(
        json.dumps([focus_doc("1", "2026 HONDA REBEL 500", condition="New")])
    )

    assert run(out, replay=True) == ["delmastro_inventory.json: no unit has condition 'Used'"]


def test_reports_shop_a_ride_units_missing_a_field(tmp_path: Path) -> None:
    units = [unit("a", "2023 Honda CRF250F"), unit("b", "2021 Suzuki SV650", condition="New")]
    pages = {"shoparide_units.json": {"url": "https://www.shoparide.ca", "platform": "shoparide"}}
    (tmp_path / "manifest.json").write_text(json.dumps({"pages": pages}))
    (tmp_path / "shoparide_units.json").write_text(json.dumps(units))
    assert problems(tmp_path) == []

    for u in units:
        del u["sale_price_value"]
    (tmp_path / "shoparide_units.json").write_text(json.dumps(units))
    assert problems(tmp_path) == ["shoparide_units.json: no unit has 'sale_price_value'"]
