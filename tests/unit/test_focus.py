"""Focus dealer sites (Typesense-backed), e.g. Del Mastro: parsing and the source."""

import pytest
import requests

from dirtbike_tracker.infrastructure import focus
from dirtbike_tracker.infrastructure.focus import FocusSource, search_key


def doc(vid: str, name: str, condition: str = "Used", sold: int = 0, **extra: object) -> dict:
    """One Typesense document, shaped like the live index's (2026-10-16)."""
    year, *_ = name.split()
    return {
        "id": vid,
        "name": name,
        "condition": condition,
        "sold": sold,
        "model_year": int(year),
        "price": 5500,
        "msrp": 5995,
        "odometer": 6150,
        "dealer_id": "4070",
        "image": "pxl_1.jpg",
        "slug": f"used/cruiser-motorcycle/{name.lower().replace(' ', '-')}/{vid}/",
        "callout_msg": "Beginner-friendly cruiser!",
    } | extra


SOURCE = FocusSource("delmastro", "https://dealer.example", "Cavan Monaghan, ON")


def test_parses_a_used_unit_into_a_listing() -> None:
    (listing,) = SOURCE.parse([doc("751149", "2018 HONDA REBEL 500")], "rebel500")

    assert (listing.title, listing.price, listing.year, listing.km) == (
        "2018 HONDA REBEL 500",
        5500.0,
        2018,
        6150,
    )
    assert listing.url == (
        "https://dealer.example/inventory/used/cruiser-motorcycle/2018-honda-rebel-500/751149/"
    )
    assert listing.image_url == "https://dealers.focus-static.com/inventory/4070/w800/pxl_1.jpg"
    assert (listing.seller_type, listing.location, listing.description) == (
        "dealer",
        "Cavan Monaghan, ON",
        "Beginner-friendly cruiser!",
    )


def test_skips_new_sold_pending_and_other_models() -> None:
    docs = [
        doc("1", "2026 HONDA REBEL 500", condition="New"),
        doc("2", "2019 HONDA REBEL 500", sold=1),
        doc("3", "2020 HONDA REBEL 500", sold=2),  # deal pending
        doc("4", "2024 HONDA NX 500"),
        doc("5", "2021 HONDA REBEL 500", price=0, odometer=0),
    ]

    (listing,) = SOURCE.parse(docs, "rebel500")

    assert (listing.external_id, listing.price, listing.price_state, listing.km) == (
        "5",
        None,
        "unknown",
        None,
    )


def test_reads_the_search_key_from_the_page() -> None:
    page = "server: { apiKey: 'UL7mE1eAlkj', // search only\n nodes: []"

    assert search_key(page) == "UL7mE1eAlkj"
    assert search_key("<html></html>") is None


class FakeDealer:
    def __init__(self, page: str = "apiKey: 'k3y'") -> None:
        self.page = page
        self.posts: list[dict] = []

    def get(self, url: str, **_: object) -> object:
        return type("R", (), {"text": self.page, "raise_for_status": lambda self: None})()

    def post(self, url: str, params: dict, json: dict, **_: object) -> object:
        self.posts.append({"url": url, "key": params["x-typesense-api-key"], **json["searches"][0]})
        hits = [{"document": doc("1", "2018 HONDA REBEL 500")}]
        return type(
            "R",
            (),
            {
                "json": lambda self: {"results": [{"hits": hits}]},
                "raise_for_status": lambda self: None,
            },
        )()


def test_one_search_serves_every_model(monkeypatch: pytest.MonkeyPatch) -> None:
    dealer = FakeDealer()
    focus._inventory.cache_clear()
    monkeypatch.setattr(requests, "get", dealer.get)
    monkeypatch.setattr(requests, "post", dealer.post)
    source = FocusSource("delmastro", "https://dealer.example", "Cavan Monaghan, ON")

    assert len(source("rebel500", "on", 3)) == 1
    assert source("nx500", "on", 3) == []
    assert source.last_fetch_ok is True
    (post,) = dealer.posts
    assert (post["url"], post["key"], post["filter_by"]) == (
        "https://dealer.example/ts/multi_search",
        "k3y",
        "category:=[Motorcycles,Powersports]",
    )


def test_a_page_without_a_key_is_a_failed_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    focus._inventory.cache_clear()
    monkeypatch.setattr(requests, "get", FakeDealer(page="<html></html>").get)
    source = FocusSource("delmastro", "https://dealer.example", "Cavan Monaghan, ON")

    assert source("rebel500", "on", 3) == []
    assert source.last_fetch_ok is False
