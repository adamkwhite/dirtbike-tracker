"""Shop a Ride (#145): finding the build's data chunks, parsing them, and the source."""

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from dirtbike_tracker.infrastructure import shoparide
from dirtbike_tracker.infrastructure.shoparide import (
    ShopARideSource,
    chunk_files,
    data_chunk_ids,
    parse_units,
)

BASE = "https://www.shoparide.ca"
UUID = "d612f28c-66f6-4ac5-8545-da1e22685ecc"


def unit(vid: str, label: str, condition: str = "Used", province: str = "Ontario", **extra: object):
    """One inventory unit, shaped like the live chunk's (2026-10-28)."""
    return {
        "vehicle_id": vid,
        "label": label,
        "year": int(label.split()[0]),
        "condition": condition,
        "url": f"/en/motocross/inventory/{vid}-{label.lower().replace(' ', '-')}/",
        "sale_price_value": 5500,
        "base_price_value": 7500,
        "usage": "50 km",
        "tenant_name": "Bay Marine",
        "tenant_city": "Trenton",
        "tenant_province": province,
        "image": {"source": "https://cdn.powergo.ca/a.webp"},
        "sort_weight": 0.5,
    } | extra


def chunk(units: list[dict]) -> str:
    """A webpack chunk holding units the way the site ships them: a JS single-quoted string."""
    text = json.dumps(units).replace("\\", "\\\\").replace("'", "\\'").replace("é", "\\xe9")
    return f"(self.webpackChunk=[]).push([[6119],{{6119:function(e){{e.exports=JSON.parse('{text}')}}}}]);"


PAGE = (
    '<script src="/_next/static/chunks/framework-1.js"></script>'
    '<script src="/_next/static/chunks/7310-ab.js"></script>'
    '<script src="/_next/static/chunks/webpack-fad5.js"></script>'
)
MAP_SCRIPT = (
    f'{{"./vehicles-data-motocross-{UUID}-en.json":[6119,6119],'
    f'"./vehicles-data-motocross-fd58b09c-ba98-4343-a1b0-5c246814a4de-fr.json":[5555,5555],'
    f'"./vehicles-data-motorcycle-{UUID}-en.json":[1734,1734]}}'
)
RUNTIME = (
    'l.u=function(e){return"static/chunks/"+e+"."+({1734:"2190",5555:"2496",6119:"26b8"})[e]+".js"}'
)


class FakeSite:
    def __init__(self, motocross: list[dict], motorcycle: list[dict]) -> None:
        self.pages = {
            f"{BASE}/en/motorcycle/": PAGE,
            f"{BASE}/_next/static/chunks/framework-1.js": "react",
            f"{BASE}/_next/static/chunks/7310-ab.js": MAP_SCRIPT,
            f"{BASE}/_next/static/chunks/webpack-fad5.js": RUNTIME,
            f"{BASE}/_next/static/chunks/6119.26b8.js": chunk(motocross),
            f"{BASE}/_next/static/chunks/1734.2190.js": chunk(motorcycle),
        }
        self.got: list[str] = []

    def get(self, url: str, **_: object) -> SimpleNamespace:
        self.got.append(url)
        if url not in self.pages:
            raise requests.HTTPError(f"404 {url}")
        return SimpleNamespace(text=self.pages[url], raise_for_status=lambda: None)


@pytest.fixture(autouse=True)
def fresh_run() -> Iterator[None]:
    shoparide._units.cache_clear()
    yield
    shoparide._units.cache_clear()


def test_parses_the_chunk_string_with_its_escapes() -> None:
    units = [unit("1", "2023 Honda CRF250F", tenant_name="Moto Sainte-Julienne é", label2="it's")]

    assert parse_units(chunk(units)) == units
    assert parse_units("no data here") == []


def test_finds_english_chunks_by_type_name_and_their_files() -> None:
    assert data_chunk_ids(MAP_SCRIPT) == {"motocross": "6119", "motorcycle": "1734"}
    assert chunk_files(RUNTIME)["6119"] == "6119.26b8.js"


def test_source_lists_used_ontario_units_of_the_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    site = FakeSite(
        [
            unit("a", "2023 Honda CRF250F"),
            unit("b", "2023 Honda CRF250F", province="Quebec"),
            unit("c", "2026 Honda CRF250F", condition="New"),
            unit("d", "2022 Kawasaki KX450"),
        ],
        [unit("e", "2021 Suzuki SV650", usage="12,000 km")],
    )
    monkeypatch.setattr(requests, "get", site.get)
    source = ShopARideSource("shoparide", BASE, cache_dir=tmp_path)

    (listing,) = source("crf250f", "ontario", 1)
    (sv,) = source("sv650", "ontario", 1)

    assert source.last_fetch_ok
    assert (listing.external_id, listing.price, listing.year, listing.km) == ("a", 5500.0, 2023, 50)
    assert listing.url == f"{BASE}/en/motocross/inventory/a-2023-honda-crf250f/"
    assert (listing.location, listing.seller_type) == ("Trenton, Ontario", "dealer")
    assert listing.description == "Dealer: Bay Marine. Usage: 50 km"
    assert sv.km == 12000
    assert sum(u.endswith(".js") and "/chunks/1" in u for u in site.got) == 1  # once a run


def test_a_build_seen_before_reads_the_disk_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    site = FakeSite(
        [unit("a", "2023 Honda CRF250F", usage="40 hours")], [unit("e", "2021 Suzuki SV650")]
    )
    monkeypatch.setattr(requests, "get", site.get)
    ShopARideSource("shoparide", BASE, cache_dir=tmp_path)("crf250f", "ontario", 1)
    shoparide._units.cache_clear()  # the next cron run
    site.got.clear()

    (listing,) = ShopARideSource("shoparide", BASE, cache_dir=tmp_path)("crf250f", "ontario", 1)

    assert site.got == [f"{BASE}/en/motorcycle/"]  # the page only: same build
    assert listing.km is None  # hours aren't km


def test_a_build_without_the_data_map_is_a_failed_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    site = FakeSite([], [])
    site.pages[f"{BASE}/_next/static/chunks/7310-ab.js"] = "renamed"
    monkeypatch.setattr(requests, "get", site.get)
    source = ShopARideSource("shoparide", BASE, cache_dir=tmp_path)

    assert source("crf250f", "ontario", 1) == []
    assert not source.last_fetch_ok
