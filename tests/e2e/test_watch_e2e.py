"""`dirtbike watch` end to end (#29): only the HTTP edge is faked.

Real scrapers, parsers, SQLite (in tmp_path), scoring, full-ad fetch and the
ntfy client all run. requests.get serves canned Kijiji / AutoTrader pages by
URL; requests.post records ntfy pushes. Nothing touches data/ (shared with prod).
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import requests
from click.testing import CliRunner

from dirtbike_tracker.application import cli as cli_module
from dirtbike_tracker.infrastructure import focus, notifier, storage

DEAL_ID, MID_ID, HIGH_ID = "1700000001", "1700000002", "1700000003"
CUT_SNIPPET = "Good little bike, starts first kick, kids outgrew it ..."  # truncated: fetch the ad


def _kijiji_search(prices: dict[str, float]) -> str:
    cards = "".join(
        '<div data-testid="listing-card">'
        f'<a href="/v-dirt-bikes-motocross/barrie/crf150f/{ad_id}"><h3>2008 Honda CRF150F</h3></a>'
        f'<p data-testid="listing-price">${price:,.2f}</p>'
        '<p data-testid="listing-location">Barrie</p></div>'
        for ad_id, price in prices.items()
    )
    ads = [
        {
            "id": ad_id,
            "description": CUT_SNIPPET,
            "activationDate": "2026-10-10T12:00:00.000Z",
            "attributes": {"all": [{"canonicalName": "caryear", "canonicalValues": ["2008"]}]},
            "imageUrls": [f"https://media.kijiji.ca/{ad_id}.jpg"],
        }
        for ad_id in prices
    ]
    payload = json.dumps({"props": {"pageProps": {"ads": ads}}})
    return f'{cards}<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def _kijiji_ad(ad_id: str, body: str) -> str:
    cache = {f"StandardListing:{ad_id}": {"description": body}}
    payload = json.dumps({"props": {"pageProps": {"__APOLLO_STATE__": cache}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def _autotrader_search() -> str:
    listing = {
        "id": "at-1",
        "url": "https://www.autotrader.ca/motorcycles-atvs/offers/honda-crf150f-at-1",
        "price": {"priceRaw": 2200},
        "location": {"city": "Oakville"},
        "description": "Runs great",
        "images": [],
        "vehicle": {"make": "Honda", "model": "CRF150F", "modelYear": 2012, "offerType": "U"},
    }
    payload = json.dumps({"props": {"pageProps": {"listings": [listing]}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def _delmastro_new_unit(price: float) -> dict:
    """One new CRF150F in Del Mastro's Typesense index, MSRP 5000."""
    return {
        "id": "13847005", "name": "2026 Honda CRF150F", "condition": "New", "sold": 0,
        "price": price, "msrp": 5000, "slug": "new/dirt-bike/2026-honda-crf150f/13847005/",
    }  # fmt: skip


@dataclass
class FakeResponse:
    text: str = ""
    status_code: int = 200
    payload: dict | None = None

    def json(self) -> dict:
        return self.payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)  # type: ignore[arg-type]


@dataclass
class FakeWeb:
    """The sites and ntfy, as seen through requests.get / requests.post."""

    prices: dict[str, float] = field(
        default_factory=lambda: {DEAL_ID: 1400, MID_ID: 2000, HIGH_ID: 2600}
    )
    ad_body: str = "Runs well, ownership in hand. Selling because the kids outgrew it."
    ntfy_status: int = 200
    new_unit_price: float = 5000  # Del Mastro's new CRF150F
    fetched: list[str] = field(default_factory=list)
    pushes: list[dict] = field(default_factory=list)

    def get(self, url: str, **_: object) -> FakeResponse:
        self.fetched.append(url)
        if "kijiji.ca/b-" in url:  # search: one page of results, then an empty page
            return FakeResponse("" if "/page-" in url else _kijiji_search(self.prices))
        if "kijiji.ca/v-" in url:  # one ad page
            return FakeResponse(_kijiji_ad(url.rsplit("/", 1)[-1], self.ad_body))
        if "autotrader.ca/motorcycles-atvs/lst" in url:
            return FakeResponse(_autotrader_search())
        if "delmastro.ca/inventory/" in url:  # the page that carries the search key
            return FakeResponse("apiKey: 'k3y'")
        raise AssertionError(f"unexpected GET {url}")

    def post(
        self, url: str, data: bytes = b"", headers: dict | None = None, **_: object
    ) -> FakeResponse:
        if url.startswith("https://www.delmastro.ca/ts/"):  # its index: one new unit
            hit = {"document": _delmastro_new_unit(self.new_unit_price)}
            return FakeResponse(payload={"results": [{"hits": [hit]}]})
        assert url == "https://ntfy.sh/e2e-topic", url
        headers = headers or {}
        if self.ntfy_status == 200:
            self.pushes.append({**headers, "body": data.decode()})
        return FakeResponse(status_code=self.ntfy_status)

    def titles(self) -> list[str]:
        return [p["Title"] for p in self.pushes]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeWeb:
    fake = FakeWeb()
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "post", fake.post)
    monkeypatch.setattr("time.sleep", lambda _: None)  # scrapers' rate limit
    focus._inventory.cache_clear()  # per-process cache: start each test from the fake site

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "listings.db")
    monkeypatch.setattr(cli_module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cli_module, "DASHBOARD_PATH", tmp_path / "dashboard.html")
    config = tmp_path / "models.yml"
    config.write_text(
        "priority:\n  - slug: crf150f\n    goal: project\n    sources: [kijiji, autotrader]\n"
        # new stock only: groups [] keeps its used inventory out of crf150f's sources
        "dealers:\n  delmastro:\n    platform: focus\n    url: https://www.delmastro.ca\n"
        "    location: Cavan Monaghan, ON\n    new_stock: true\n    groups: []\n"
    )
    monkeypatch.setattr(cli_module, "CONFIG_PATH", config)
    monkeypatch.setattr(notifier, "NTFY_TOPIC", "e2e-topic")
    monkeypatch.setattr(notifier, "HEALTHCHECK_URL", "")
    return fake


def _watch() -> str:
    focus._inventory.cache_clear()  # each cron run is a new process
    result = CliRunner().invoke(cli_module.cli, ["watch"])
    assert result.exit_code == 0, result.output
    return str(result.output)


def test_new_deal_alerts_once_with_photo_and_specs(web: FakeWeb) -> None:
    _watch()
    _watch()

    # median of 1400 / 2000 / 2600 / 2200 is 2100, so only the 1400 bike is a DEAL
    assert web.titles() == ["DEAL: CRF150F $1400 (-33% vs median)"]
    push = web.pushes[0]
    assert push["Attach"] == f"https://media.kijiji.ca/{DEAL_ID}.jpg"
    assert push["Click"].endswith(f"/crf150f/{DEAL_ID}")
    assert push["body"].splitlines()[1] == "2008"
    assert sum(f"/v-dirt-bikes-motocross/barrie/crf150f/{DEAL_ID}" in u for u in web.fetched) == 1


def test_red_flag_in_ad_body_blocks_the_deal(web: FakeWeb) -> None:
    web.ad_body = "Runs, but no ownership. Cash only."  # the snippet alone looks clean

    output = _watch()

    assert web.pushes == []
    assert "DEAL alerts: 0" in output


def test_price_drop_on_alerted_listing_sends_drop_push(web: FakeWeb) -> None:
    _watch()
    web.prices[DEAL_ID] = 1300

    _watch()

    assert web.titles() == [
        "DEAL: CRF150F $1400 (-33% vs median)",
        "PRICE DROP: DEAL: CRF150F $1300 (-38% vs median)",
    ]
    assert web.pushes[1]["body"].startswith("Was $1400. ")


def test_failed_push_is_not_recorded_and_retries_next_run(web: FakeWeb) -> None:
    web.ntfy_status = 500
    _watch()
    assert web.pushes == []

    web.ntfy_status = 200
    _watch()

    assert web.titles() == ["DEAL: CRF150F $1400 (-33% vs median)"]


def test_db_state_after_a_run(web: FakeWeb, tmp_path: Path) -> None:
    _watch()

    with storage.connect(tmp_path / "listings.db") as conn:
        rows = {
            r["external_id"]: dict(r)
            for r in conn.execute(
                "SELECT external_id, source, price, year, image_url, full_description,"
                " detail_fetched_at FROM listings"
            )
        }
        history = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        tags = {r[0] for r in conn.execute("SELECT DISTINCT model_slug FROM listing_models")}

    assert set(rows) == {DEAL_ID, MID_ID, HIGH_ID, "at-1"}
    assert (history, tags) == (4, {"crf150f"})
    assert rows[DEAL_ID]["full_description"] == web.ad_body  # read before alerting (#71)
    assert rows[HIGH_ID]["detail_fetched_at"] is None  # HIGH: not worth a fetch
    assert (rows["at-1"]["source"], rows["at-1"]["year"]) == ("autotrader", 2012)
    assert (tmp_path / "dashboard.html").exists()
    assert (tmp_path / "state" / "notified_ids.txt").read_text() == f"kijiji:{DEAL_ID}"


def test_shared_db_guard_is_active(web: FakeWeb) -> None:  # tests/conftest.py
    shared = Path(cli_module.ROOT) / "data" / "listings.db"

    with pytest.raises(AssertionError, match="shared data/ DB"):
        sqlite3.connect(shared)


def test_a_quiet_run_lists_the_deal_it_skips_and_marks_it_sent(web: FakeWeb) -> None:
    result = CliRunner().invoke(cli_module.cli, ["watch", "--no-notify"])

    assert result.exit_code == 0, result.output
    assert web.pushes == []
    assert "quiet deals: crf150f $1400 2008 Honda CRF150F" in result.output
    _watch()  # the next cron run
    assert web.pushes == []  # the seed recorded it; pass it on from the quiet line instead


def test_a_dealer_cutting_a_new_units_price_alerts_once(web: FakeWeb) -> None:
    _watch()  # the first run records the dealers' new stock
    web.new_unit_price = 4500

    _watch()
    _watch()

    markdowns = [t for t in web.titles() if t.startswith("MARKDOWN")]
    assert markdowns == ["MARKDOWN: CRF150F $4,500 (save $500)"]
    assert "new stock fetch failed" not in _watch()
