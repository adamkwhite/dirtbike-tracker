"""DEAL / GOOD message formatting, sent to a spy Notifier (no patching)."""

import pytest

from dirtbike_tracker.application.alerts import notify_deal, notify_good

from .conftest import PushSpy

LISTING = {"price": 2000, "delta_pct": -20.0, "model_slug": "crf150f", "title": "t", "url": "u"}


@pytest.mark.parametrize(
    ("urgency", "priority", "title_start"),
    [("URGENT", 5, "URGENT DEAL: CRF150F $2000"), (None, 4, "DEAL: CRF150F $2000")],
)
def test_notify_deal_priority(
    sent: PushSpy, urgency: str | None, priority: int, title_start: str
) -> None:
    notify_deal(sent, {**LISTING, "urgency": urgency})

    assert sent[0]["priority"] == priority
    assert sent[0]["title"].startswith(title_start)


def test_notify_good_negotiable_suggests_offer(sent: PushSpy) -> None:
    notify_good(sent, {**LISTING, "urgency": "NEGOTIABLE", "age_days": 21.4})

    assert sent[0]["priority"] == 3
    assert sent[0]["title"] == "Negotiable: CRF150F $2000 (-20%), listed 21d"
    assert sent[0]["body"].startswith("Listed 21 days. Try ~$1800.")


def test_notify_good_plain_is_low_priority(sent: PushSpy) -> None:
    notify_good(sent, {**LISTING, "urgency": None})

    assert sent[0]["priority"] == 2
    assert sent[0]["title"] == "Good: CRF150F $2000 (-20%)"


def test_price_drop_prefix_and_was(sent: PushSpy) -> None:
    notify_good(sent, {**LISTING, "urgency": None, "price_drop_from": 2400})

    assert sent[0]["title"] == "PRICE DROP: Good: CRF150F $2000 (-20%)"
    assert sent[0]["body"].startswith("Was $2400. ")


def test_deal_says_which_median(sent: PushSpy) -> None:
    notify_deal(sent, {**LISTING, "basis": "project"})

    assert "(-20% vs project median)" in sent[0]["title"]


def test_alert_body_has_year_and_km(sent: PushSpy) -> None:
    notify_deal(sent, {**LISTING, "location": "Kingston", "year": 2018, "km": 16672})

    assert sent[0]["body"] == "t\n2018 · 16,672 km\nKingston\nu"


def test_alerts_attach_the_listing_photo(sent: PushSpy) -> None:
    notify_deal(sent, {**LISTING, "image_url": "https://media.kijiji.ca/a.jpg"})
    notify_good(sent, LISTING)

    assert sent[0]["attach_url"] == "https://media.kijiji.ca/a.jpg"
    assert sent[1]["attach_url"] is None
