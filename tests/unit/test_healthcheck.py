from types import SimpleNamespace

import pytest
import requests

from dirtbike_tracker.infrastructure import notifier


def test_ping_is_noop_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier, "HEALTHCHECK_URL", "")
    monkeypatch.setattr(notifier.requests, "get", lambda *a, **k: pytest.fail("pinged"))

    assert notifier.ping_healthcheck() is False


def test_ping_hits_configured_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, **_: object) -> SimpleNamespace:
        calls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(notifier, "HEALTHCHECK_URL", "https://hc-ping.com/test-uuid")
    monkeypatch.setattr(notifier.requests, "get", fake_get)

    assert notifier.ping_healthcheck() is True
    assert calls == ["https://hc-ping.com/test-uuid"]


def test_ping_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object, **__: object) -> None:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(notifier.requests, "get", boom)

    assert notifier.ping_healthcheck("https://hc-ping.com/test-uuid") is False
