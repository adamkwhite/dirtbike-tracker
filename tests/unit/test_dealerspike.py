"""Dealer Spike inventory files (#99), e.g. Sturgess Cycle: parsing and the watch-flow hooks."""

import json
from collections import Counter
from dataclasses import replace

import pytest
import requests

from dirtbike_tracker.application.ports import App
from dirtbike_tracker.application.watch import sources_seen
from dirtbike_tracker.infrastructure import dealerspike
from dirtbike_tracker.infrastructure.dealerspike import DealerSpikeSource, parse_vehicles


def _unit(unit_id: str, year: str, manuf: str, model: str, **extra: str) -> dict:
    return {
        "id": unit_id,
        "bike_year": year,
        "manuf": manuf,
        "model": model,
        "price": "7999",
        "type": "U",
        "miles": "1200",
        "DateAdded": "1783145477",
        "bike_image": "AB6E156B-8E0B-419C-B26C-AA79829A3C28.jpg",
        "notes": "Clean, serviced",
    } | extra


def _file(*units: dict) -> str:
    return "var V6SpbFeeDataVersion=2;\nvar Vehicles=" + json.dumps(list(units)) + ";\n"


INVENTORY = _file(
    _unit("1", "2025", "Honda®", "Rebel 500 ABS"),
    _unit("2", "2024", "HONDA", "Rebel 500", miles="0", price="6,500"),
    _unit("3", "2026", "Honda®", "Rebel 500", type="N"),  # new stock
    _unit("4", "2025", "Kawasaki", "Ninja 500"),  # another model
)


def _source() -> DealerSpikeSource:
    return DealerSpikeSource("sturgess", "https://dealer.example", 5620, "Hamilton, ON")


SOURCE = _source()


def test_parses_used_units_for_the_model() -> None:
    listings = {x.external_id: x for x in SOURCE.parse(INVENTORY, "rebel500")}

    assert sorted(listings) == ["1", "2"]
    rebel = listings["1"]
    assert (rebel.title, rebel.price, rebel.year, rebel.km) == (
        "2025 Honda Rebel 500 ABS",
        7999.0,
        2025,
        1200,
    )
    assert (rebel.seller_type, rebel.location, rebel.posted_text) == (
        "dealer",
        "Hamilton, ON",
        "2026-07-04T06:11:17",
    )
    assert rebel.url == "https://dealer.example/default.asp?page=xPreOwnedInventoryDetail&id=1"
    assert rebel.image_url == (
        "https://cdn.dealerspike.com/imglib/v1/300x225/imglib/Assets/Inventory"
        "/AB/6E/AB6E156B-8E0B-419C-B26C-AA79829A3C28.jpg"
    )
    assert (listings["2"].title, listings["2"].km) == (
        "2024 Honda Rebel 500",
        None,
    )  # 0 km: unknown
    assert listings["2"].price == 6500.0  # "6,500": commas stripped


def test_include_new_keeps_new_stock_with_its_detail_page() -> None:
    new = [x for x in SOURCE.parse(INVENTORY, "rebel500", include_new=True) if x.external_id == "3"]

    assert [x.url for x in new] == [
        "https://dealer.example/default.asp?page=xNewInventoryDetail&id=3"
    ]


def test_include_new_reads_the_all_units_file() -> None:
    assert SOURCE.inventory_url().endswith("/imglib/Inventory/cache/5620/UVehInv.js")
    assert SOURCE.inventory_url(include_new=True).endswith("/5620/VehInv.js")


@pytest.mark.parametrize("text", ["", "var Vehicles={};", "var Vehicles=[not json"])
def test_a_reshaped_file_parses_to_nothing(text: str) -> None:
    assert parse_vehicles(text) == []


def test_fetch_failure_returns_nothing_and_marks_the_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(url: str, **_: object) -> object:
        raise requests.ConnectionError("dealer site down")

    dealerspike._fetch.cache_clear()
    monkeypatch.setattr(requests, "get", down)
    source = _source()

    assert source("rebel500", "ontario", 3) == []
    assert source.last_fetch_ok is False


def test_one_fetch_serves_every_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def get(url: str, **_: object) -> object:
        fetched.append(url)
        body = ("﻿" + INVENTORY).encode()
        return type("R", (), {"content": body, "raise_for_status": lambda self: None})()

    dealerspike._fetch.cache_clear()
    monkeypatch.setattr(requests, "get", get)
    source = _source()

    assert len(source("rebel500", "ontario", 3)) == 2
    assert source("crf150f", "ontario", 3) == []
    assert (len(fetched), source.last_fetch_ok) == (1, True)


def test_a_dealer_that_answered_counts_as_seen_with_no_matches(app: App) -> None:
    answered, failed = _source(), _source()
    answered.last_fetch_ok = True
    app = replace(
        app, sources={"sturgess": answered, "other": failed, "kijiji": lambda *_, **__: []}
    )

    seen = sources_seen(app, Counter({"sturgess": 0, "other": 0, "kijiji": 0}))

    # a dealer with none of our models still answered: its old listings are sold, not missed
    assert seen == Counter({"sturgess": 1, "other": 0, "kijiji": 0})
