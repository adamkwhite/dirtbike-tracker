"""The drift checks report breakage, on small synthetic recordings (no network)."""

import json
from pathlib import Path

from click.testing import CliRunner

from dirtbike_tracker.application.cli import cli
from dirtbike_tracker.application.ports import App
from dirtbike_tracker.infrastructure.drift import problems

from .conftest import PushSpy


def _kijiji_search(fields: list[dict]) -> str:
    """One Rebel card plus __NEXT_DATA__ carrying the given form fields."""
    card = (
        '<div data-testid="listing-card">'
        '<a href="/v-street-cruisers-choppers/barrie/rebel/1700000001"><h3>2019 Honda Rebel 500</h3></a>'
        '<p data-testid="listing-price">$5,500.00</p></div>'
    )
    ad = {
        "id": "1700000001",
        "description": "Runs great",
        "activationDate": "2026-10-10T12:00:00.000Z",
        "imageUrls": ["https://media.kijiji.ca/a.jpg"],
        "attributes": {"all": fields},
    }
    payload = json.dumps({"props": {"pageProps": {"ads": [ad]}}})
    return f'{card}<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def _record(out: Path, html: str) -> Path:
    out.mkdir()
    (out / "kijiji_search_rebel500.html").write_text(html)
    pages = {"kijiji_search_rebel500.html": {"url": "u", "slug": "rebel500"}}
    (out / "manifest.json").write_text(json.dumps({"pages": pages}))
    return out


YEAR = {"canonicalName": "caryear", "canonicalValues": ["2019"]}
KM = {"canonicalName": "carmileageinkms", "canonicalValues": ["12000"]}


def test_clean_recording_has_no_problems(tmp_path: Path) -> None:
    assert problems(_record(tmp_path / "rec", _kijiji_search([YEAR, KM]))) == []


def test_reports_a_field_that_stopped_parsing(tmp_path: Path) -> None:
    out = _record(tmp_path / "rec", _kijiji_search([YEAR]))  # km field renamed / gone

    assert problems(out) == ["kijiji_search_rebel500.html: carmileageinkms stopped parsing"]


def test_reports_a_page_that_parses_nothing(tmp_path: Path) -> None:
    out = _record(tmp_path / "rec", "<html>new layout</html>")

    assert problems(out) == ["kijiji_search_rebel500.html: parsed no listings"]


def test_drift_command_alerts_and_fails_on_problems(
    app: App, sent: PushSpy, tmp_path: Path
) -> None:
    out = _record(tmp_path / "rec", "<html>new layout</html>")

    result = CliRunner().invoke(cli, ["drift", "--replay", "--dir", str(out)], obj=app)

    assert result.exit_code == 1
    assert "parsed no listings" in result.output
    assert [p["title"] for p in sent] == ["Site drift: 1 parser problem(s)"]


def test_drift_command_ok_is_quiet(app: App, sent: PushSpy, tmp_path: Path) -> None:
    out = _record(tmp_path / "rec", _kijiji_search([YEAR, KM]))

    result = CliRunner().invoke(cli, ["drift", "--replay", "--dir", str(out)], obj=app)

    assert result.exit_code == 0, result.output
    assert sent == []
