import pytest

from dirtbike_tracker.infrastructure import notifier


def test_notify_sends_attach_header(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: dict = {}

    def fake_post(url: str, data: bytes, headers: dict, timeout: int) -> object:
        posted.update(headers)
        return type("R", (), {"ok": True, "raise_for_status": lambda self: None})()

    monkeypatch.setattr(notifier.requests, "post", fake_post)

    notifier.notify("t", "b", topic="x", attach_url="https://media.kijiji.ca/a.jpg")

    assert posted["Attach"] == "https://media.kijiji.ca/a.jpg"
