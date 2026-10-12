import pytest

from dirtbike_tracker.infrastructure import notifier


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_notify(**kw: object) -> bool:
        calls.append(kw)
        return True

    monkeypatch.setattr(notifier, "notify", fake_notify)
    return calls


LISTING = {"price": 2000, "delta_pct": -20.0, "model_slug": "crf150f", "title": "t", "url": "u"}


@pytest.mark.parametrize(
    ("urgency", "priority", "title_start"),
    [("URGENT", 5, "URGENT DEAL: CRF150F $2000"), (None, 4, "DEAL: CRF150F $2000")],
)
def test_notify_deal_priority(
    sent: list[dict], urgency: str | None, priority: int, title_start: str
) -> None:
    notifier.notify_deal({**LISTING, "urgency": urgency})

    assert sent[0]["priority"] == priority
    assert sent[0]["title"].startswith(title_start)


def test_notify_good_negotiable_suggests_offer(sent: list[dict]) -> None:
    notifier.notify_good({**LISTING, "urgency": "NEGOTIABLE", "age_days": 21.4})

    assert sent[0]["priority"] == 3
    assert sent[0]["title"] == "Negotiable: CRF150F $2000 (-20%), listed 21d"
    assert sent[0]["body"].startswith("Listed 21 days. Try ~$1800.")


def test_notify_good_plain_is_low_priority(sent: list[dict]) -> None:
    notifier.notify_good({**LISTING, "urgency": None})

    assert sent[0]["priority"] == 2
    assert sent[0]["title"] == "Good: CRF150F $2000 (-20%)"


def test_price_drop_prefix_and_was(sent: list[dict]) -> None:
    notifier.notify_good({**LISTING, "urgency": None, "price_drop_from": 2400})

    assert sent[0]["title"] == "PRICE DROP: Good: CRF150F $2000 (-20%)"
    assert sent[0]["body"].startswith("Was $2400. ")


def test_deal_says_which_median(sent: list[dict]) -> None:
    notifier.notify_deal({**LISTING, "basis": "project"})

    assert "(-20% vs project median)" in sent[0]["title"]
