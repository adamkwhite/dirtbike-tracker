"""dirtbike serve's HTTP layer on a real localhost socket, with a fake hide callback."""

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path

import pytest

from dirtbike_tracker.infrastructure.dashboard_server import make_server

HIDES: list[tuple[int, bool, str | None]] = []
NOTES: list[tuple[int, str | None]] = []
YEARS: list[tuple[int, int | None]] = []


@pytest.fixture
def port(tmp_path: Path) -> Iterator[int]:
    page = tmp_path / "dashboard.html"
    page.write_text("<p>bikes</p>")
    (tmp_path / "listings.db").write_text("secret")
    HIDES.clear()
    NOTES.clear()
    YEARS.clear()

    def hide(listing_id: int, hidden: bool, note: str | None) -> bool:
        HIDES.append((listing_id, hidden, note))
        return listing_id != 404

    def note(listing_id: int, text: str | None) -> bool:
        NOTES.append((listing_id, text))
        return listing_id != 404

    def year(listing_id: int, model_year: int | None) -> bool:
        YEARS.append((listing_id, model_year))
        return listing_id != 404

    server = make_server(page, hide, note, year, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _request(port: int, method: str, path: str, body: dict | None = None, **headers: str) -> int:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, json.dumps(body) if body else None, headers)
    status = conn.getresponse().status
    conn.close()
    return status


def test_serves_only_the_page(port: int) -> None:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/")
    assert conn.getresponse().read() == b"<p>bikes</p>"
    conn.close()

    for path in ("/listings.db", "/../listings.db", "/data/listings.db"):
        assert _request(port, "GET", path) == 404


def test_hide_needs_the_custom_header_and_valid_json(port: int) -> None:
    ok = {"id": 7, "hidden": True, "note": "too far"}

    assert _request(port, "POST", "/hide", ok) == 403  # no header: maybe another site
    assert _request(port, "POST", "/hide", {"hidden": True}, **{"X-Dirtbike": "1"}) == 400
    assert _request(port, "OPTIONS", "/hide") == 501  # no preflight, so no cross-site POST
    assert HIDES == []

    assert _request(port, "POST", "/hide", ok, **{"X-Dirtbike": "1"}) == 204
    assert _request(port, "POST", "/hide", {"id": 7, "hidden": False}, **{"X-Dirtbike": "1"}) == 204
    assert (
        _request(port, "POST", "/hide", {"id": 404, "hidden": True}, **{"X-Dirtbike": "1"}) == 404
    )
    assert HIDES[:2] == [(7, True, "too far"), (7, False, None)]


def test_note_saves_trimmed_text_and_clears_on_empty(port: int) -> None:
    h = {"X-Dirtbike": "1"}

    assert _request(port, "POST", "/note", {"id": 7, "note": "asked VIN"}) == 403
    assert _request(port, "POST", "/note", {"note": "x"}, **h) == 400
    assert _request(port, "POST", "/note", {"id": 7, "note": "  asked VIN  "}, **h) == 204
    assert _request(port, "POST", "/note", {"id": 7, "note": ""}, **h) == 204
    assert _request(port, "POST", "/note", {"id": 404, "note": "x"}, **h) == 404
    assert NOTES[:2] == [(7, "asked VIN"), (7, None)]
    assert HIDES == []


def test_year_takes_a_model_year_or_null(port: int) -> None:
    h = {"X-Dirtbike": "1"}

    assert _request(port, "POST", "/year", {"id": 7, "year": 2004}) == 403
    for bad in (1800, 2999, "abc", 20.5):
        assert _request(port, "POST", "/year", {"id": 7, "year": bad}, **h) == 400, bad
    assert _request(port, "POST", "/year", {"id": 7, "year": "2004"}, **h) == 204
    assert _request(port, "POST", "/year", {"id": 7, "year": None}, **h) == 204
    assert _request(port, "POST", "/year", {"id": 404, "year": 2004}, **h) == 404
    assert YEARS[:2] == [(7, 2004), (7, None)]
