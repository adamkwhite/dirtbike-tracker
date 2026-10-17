"""The new-bike markdown watch (application/markdowns.py), over fake dealers."""

from dataclasses import replace

from dirtbike_tracker.application.markdowns import check_markdowns, markdown_rows
from dirtbike_tracker.application.ports import App, NewStockSource
from dirtbike_tracker.domain.listing import NewUnit

from .conftest import PushSpy


def unit(price: float, was: float | None = None, uid: str = "1") -> NewUnit:
    return NewUnit(
        dealer="Burlington Cycle",
        unit_id=uid,
        model_slug="crf150f",
        title="2026 Honda CRF150F",
        price=price,
        was=was,
        url=f"https://dealer.example/{uid}",
    )


class FakeDealer:
    def __init__(self, *units: NewUnit) -> None:
        self.units = list(units)
        self.asked: list[list[str]] = []

    def __call__(self, slugs: list[str]) -> list[NewUnit]:
        self.asked.append(slugs)
        return list(self.units)


def _app(app: App, dealer: NewStockSource) -> App:
    return replace(app, new_stock={"burlington": dealer})


def test_first_run_records_quietly_then_a_price_cut_alerts_once(app: App, sent: PushSpy) -> None:
    dealer = FakeDealer(unit(5000))
    app = _app(app, dealer)

    check_markdowns(app, notify=True)
    assert sent == []  # the first run only records
    dealer.units = [unit(4600)]
    check_markdowns(app, notify=True)
    check_markdowns(app, notify=True)

    assert [p["title"] for p in sent] == ["MARKDOWN: CRF150F $4,600 (save $400)"]
    assert sent[0]["body"].startswith("Price cut, was $5,000 (8% off).")
    assert dealer.asked[0] == ["crf150f"]  # the tracked models


def test_a_new_unit_already_on_sale_alerts_and_a_regular_one_does_not(
    app: App, sent: PushSpy
) -> None:
    dealer = FakeDealer()
    app = _app(app, dealer)
    check_markdowns(app, notify=True)  # seed: nothing yet

    dealer.units = [unit(4200, was=4700, uid="sale"), unit(4959, uid="full")]
    check_markdowns(app, notify=True)

    assert [p["title"] for p in sent] == ["MARKDOWN: CRF150F $4,200 (save $500)"]


def test_an_unsent_alert_retries_next_run(app: App, sent: PushSpy) -> None:
    dealer = FakeDealer(unit(5000))
    app = _app(app, dealer)
    check_markdowns(app, notify=True)
    dealer.units = [unit(4600)]
    sent.ok = False

    check_markdowns(app, notify=True)
    sent.ok = True
    check_markdowns(app, notify=True)
    check_markdowns(app, notify=True)

    # the failed send, then its retry; PushSpy records both. Then nothing more
    assert [p["title"] for p in sent] == ["MARKDOWN: CRF150F $4,600 (save $400)"] * 2


def test_a_quiet_run_lists_the_markdown_instead_of_sending(app: App, sent: PushSpy) -> None:
    dealer = FakeDealer(unit(5000))
    app = _app(app, dealer)
    check_markdowns(app, notify=True)
    dealer.units = [unit(4600)]

    _, lines = check_markdowns(app, notify=False)
    check_markdowns(app, notify=True)

    assert sent == []  # recorded by the quiet run, not replayed
    assert lines == ["quiet markdown: crf150f $4600 2026 Honda CRF150F https://dealer.example/1"]


def test_a_dealer_that_fails_keeps_its_last_units_and_prices(app: App, sent: PushSpy) -> None:
    dealer = FakeDealer(unit(5000))
    app = _app(app, dealer)
    check_markdowns(app, notify=True)

    def down(slugs: list[str]) -> list[NewUnit]:
        raise ConnectionError("dealer site down")

    units, lines = check_markdowns(_app(app, down), notify=True)
    dealer.units = [unit(4600)]
    check_markdowns(app, notify=True)

    assert [u["price"] for u in units] == [5000]  # the dashboard keeps showing it
    assert lines == ["burlington: new stock fetch failed: dealer site down"]
    assert len(sent) == 1  # the price from before the outage still counts


def test_markdown_rows_are_the_last_runs_units(app: App) -> None:
    app = _app(app, FakeDealer(unit(5000, was=5500)))
    assert markdown_rows(app) == []

    check_markdowns(app, notify=True)

    (row,) = markdown_rows(app)
    assert (row["title"], row["price"], row["was"], row["dealer"]) == (
        "2026 Honda CRF150F",
        5000,
        5500,
        "Burlington Cycle",
    )
