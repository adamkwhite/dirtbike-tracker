"""ARI dealer sites (Burlington Cycle): new-stock cards for the markdown watch."""

import json

import pytest
import requests

from dirtbike_tracker.infrastructure.ari import AriNewStock, parse_units


def card(
    pid: int, model: str, price: float, regular: float, on_sale: bool = False, **extra: object
) -> str:
    """One search-result card with its hidden JSON, shaped like the live site's (2026-10-16)."""
    unit = {
        "item": f"2026 {model} - Kawasaki",
        "productId": pid,
        "itemYear": 2026,
        "itemMake": "Kawasaki",
        "itemModel": model,
        "itemPrice": price,
        "unitPrice": regular,
        "itemOnSale": on_sale,
        "itemOriginalPrice": "$9,099.00",  # base MSRP without freight: not what we compare
        "usageStatus": "New",
        "itemUrl": f"//www.dealer.example/new-models/{pid}i",
        "itemThumbNailUrl": f"//cdn.example/{pid}.jpg",
    } | extra
    return (
        '<div class="search-result-grid panel-body">'
        f'<span class="datasource hidden">{json.dumps(unit)}</span></div>'
    )


def page(*cards: str) -> str:
    return f"<html><body>{''.join(cards)}</body></html>"


SLUGS = ["vulcans", "klx140", "crf150f"]


class FakeSite:
    def __init__(self, *pages: str) -> None:
        self.pages = list(pages)
        self.fetched: list[str] = []

    def get(self, url: str, **_: object) -> object:
        self.fetched.append(url)
        n = int(url.rsplit("/page/", 1)[1]) if "/page/" in url else 1
        if n > len(self.pages):
            return type("R", (), {"status_code": 404, "text": "", "raise_for_status": _raise})()
        text = self.pages[n - 1]
        return type(
            "R", (), {"status_code": 200, "text": text, "raise_for_status": lambda self: None}
        )()


def _raise(self: object) -> None:
    raise requests.HTTPError("404")


def test_tracked_new_units_with_the_sale_price_and_regular_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = FakeSite(
        page(
            card(1, "Vulcan® S", 10704, 10704),
            card(2, "KLX®140R F", 5304, 5804, on_sale=True),
            card(3, "Ninja® 650", 9714, 10414, on_sale=True),  # not tracked
        ),
        page(card(4, "KLX®140R F", 5804, 5804, usageStatus="Used")),
    )
    monkeypatch.setattr(requests, "get", site.get)

    units = AriNewStock("Burlington Cycle", "https://www.dealer.example")(SLUGS)

    assert [(u.model_slug, u.title, u.price, u.was) for u in units] == [
        ("vulcans", "2026 Kawasaki Vulcan S", 10704.0, None),
        ("klx140", "2026 Kawasaki KLX140R F", 5304.0, 5804.0),
    ]
    assert units[1].url == "https://www.dealer.example/new-models/2i"
    assert units[1].image_url == "https://cdn.example/2.jpg"
    assert site.fetched == [
        "https://www.dealer.example/search/inventory/availability/In%20Stock/usage/New",
        "https://www.dealer.example/search/inventory/availability/In%20Stock/usage/New/page/2",
        "https://www.dealer.example/search/inventory/availability/In%20Stock/usage/New/page/3",
    ]  # page 3 is past the end (404)


def test_a_page_that_repeats_ends_the_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    same = page(card(1, "Vulcan® S", 10704, 10704))
    site = FakeSite(same, same, same)
    monkeypatch.setattr(requests, "get", site.get)

    assert len(AriNewStock("B", "https://www.dealer.example")(SLUGS)) == 1
    assert len(site.fetched) == 2


def test_a_site_that_is_down_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "get", FakeSite().get)  # 404 on the first page
    source = AriNewStock("B", "https://www.dealer.example")

    with pytest.raises(requests.HTTPError):
        source(SLUGS)


@pytest.mark.parametrize(
    "html", ["", '<span class="datasource">not json</span>', '<span class="datasource">[]</span>']
)
def test_a_reshaped_page_parses_to_nothing(html: str) -> None:
    assert parse_units(html) == []
