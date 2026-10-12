"""Renderers take a MarketSnapshot, so they test without a DB."""

from pathlib import Path

from dirtbike_tracker.domain.scoring import MarketSnapshot
from dirtbike_tracker.infrastructure.dashboard import render_dashboard, write_dashboard
from dirtbike_tracker.infrastructure.reporter import render_report, write_report


def test_render_report_groups_by_rating(snap: MarketSnapshot) -> None:
    md = render_report(snap)

    assert "# CRF150F market snapshot" in md
    assert "- Median: **$2000**" in md
    assert "▼ 12.5%" in md
    assert "### DEAL (1)" in md
    assert "### UNKNOWN (1)" in md


def test_render_report_without_market_data(snap: MarketSnapshot) -> None:
    empty = MarketSnapshot(slug="ttr110", stats={"count": 0}, listings=[], trend=None, prices=[])

    assert "No data. Scrape first." in render_report(empty)


def test_write_report(snap: MarketSnapshot, tmp_path: Path) -> None:
    path = write_report(snap, out_dir=tmp_path)

    assert path.name.startswith("crf150f_")
    assert path.read_text() == render_report(snap)


def test_dashboard_renders_section_and_nav(snap: MarketSnapshot, tmp_path: Path) -> None:
    path = write_dashboard([snap], tmp_path / "dashboard.html")

    page = path.read_text()
    assert page == render_dashboard([snap])
    assert 'id="crf150f"' in page
    assert 'href="#crf150f"' in page
    assert "CRF150F at 1400" in page
    assert "Contact" in page


def test_dashboard_shows_recently_sold(snap: MarketSnapshot) -> None:
    snap.sold = [
        {
            "price": 3600.0,
            "days_listed": 12,
            "location": "Oakville, ON",
            "sold_at": "2026-10-09T14:00:00",
            "url": "https://example.com/sold",
            "title": "2017 Honda CRF150F",
        }
    ]

    page = render_dashboard([snap])

    assert "Recently sold (likely)" in page
    assert "$3600" in page and "12d" in page and "2026-10-09" in page
