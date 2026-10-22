"""Renderers take a MarketSnapshot, so they test without a DB."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from dirtbike_tracker.domain.scoring import MarketSnapshot, score_listings
from dirtbike_tracker.infrastructure.dashboard import (
    _listing_row,
    _model_section,
    render_dashboard,
    write_dashboard,
)
from dirtbike_tracker.infrastructure.reporter import (
    contact_label,
    location_label,
    render_report,
    write_report,
)


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


def test_dashboard_without_market_data() -> None:
    empty = MarketSnapshot(slug="ttr110", stats={"count": 0}, listings=[], trend=None, prices=[])

    html = _model_section(empty)

    assert "No listings yet" in html
    assert "No active listings" in html


def test_write_report(snap: MarketSnapshot, tmp_path: Path) -> None:
    path = write_report(snap, out_dir=tmp_path)

    assert path.name.startswith("crf150f_")
    assert path.read_text() == render_report(snap)


def test_dashboard_renders_section_and_nav(snap: MarketSnapshot, tmp_path: Path) -> None:
    path = write_dashboard({"Dirt": [snap]}, tmp_path / "dashboard.html")

    page = path.read_text()
    assert page == render_dashboard({"Dirt": [snap]})
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

    page = render_dashboard({"Dirt": [snap]})

    assert "Recently sold (likely)" in page
    assert "$3600" in page
    assert "12d" in page
    assert "2026-10-09" in page


def test_dashboard_weekly_chart_needs_two_weeks(snap: MarketSnapshot) -> None:
    snap.weekly = [("2026-09-28", None), ("2026-10-05", 3100.0)]
    assert "Weekly median chart appears once" in render_dashboard({"Dirt": [snap]})

    snap.weekly = [("2026-09-28", 3000.0), ("2026-10-05", 3100.0)]
    page = render_dashboard({"Dirt": [snap]})
    assert 'id="trend-crf150f"' in page
    assert '"data": [3000.0, 3100.0]' in page


def test_dashboard_has_inline_favicon(snap: MarketSnapshot) -> None:
    assert '<link rel="icon" href="data:image/svg+xml,' in render_dashboard({"Dirt": [snap]})


def test_listing_rows_show_days_on_market(snap: MarketSnapshot) -> None:
    snap.listings = [{**snap.listings[0], "age_days": 14.7}]

    assert "Barrie (14d)" in render_dashboard({"Dirt": [snap]})
    assert "Barrie (14d)" in render_report(snap)


def test_location_label_without_age() -> None:
    assert location_label({"location": "Barrie, ON"}, 28) == "Barrie"


def test_nav_shows_active_listing_count_per_model(snap: MarketSnapshot) -> None:
    assert f'<a href="#crf150f">CRF150F ({len(snap.listings)})</a>' in render_dashboard(
        {"Dirt": [snap]}
    )


def test_dashboard_shows_fit_with_flags_tooltip() -> None:
    (listing,) = score_listings(
        [{"price": 2000, "title": "CRF150F project, runs great", "location": "", "url": "u"}], 2000
    )

    html = _model_section(
        MarketSnapshot(
            slug="crf150f", stats={"count": 0}, listings=[listing], trend=None, prices=[]
        )
    )

    assert '<td class="num" title="+project">7</td>' in html


def test_dashboard_shows_project_median_tile_and_marks_fallback() -> None:
    (listing,) = score_listings(
        [{"price": 1000, "title": "CRF150F project", "location": "", "url": "u"}], 2000
    )
    stats = {"count": 6, "low": 1000, "median": 2000, "high": 3000}

    with_projects = _model_section(
        MarketSnapshot(
            slug="crf150f",
            stats={**stats, "project_median": 1200, "project_count": 5},
            listings=[listing],
            trend=None,
            prices=[],
        )
    )
    without = _model_section(
        MarketSnapshot(slug="crf150f", stats=stats, listings=[listing], trend=None, prices=[])
    )

    assert 'Project median</div><div class="val">$1200' in with_projects
    assert "Project median" not in without
    assert ">-50%*</td>" in without


def test_contacted_badge_with_note_tooltip() -> None:
    listing = {
        "price": 1200, "rating": "DEAL", "delta_pct": -61.0, "fit": 9, "fit_flags": [],
        "title": "2003 honda crf150f", "location": "Muskoka", "url": "u",
        "contacted_days": 11.4, "contact_note": "offered 750",
    }  # fmt: skip

    html = _listing_row(listing)

    assert (
        '<span class="contacted" title="contacted 11d ago: offered 750">contacted 11d ago</span>'
        in html
    )
    assert (
        contact_label({**listing, "contacted_days": 0.2, "contact_note": None}) == "contacted today"
    )
    assert contact_label({"contacted_days": None}) is None


def test_row_shows_year_km_and_price_per_year() -> None:
    listing = {
        "price": 5250, "rating": "GOOD", "delta_pct": -12.0, "fit": 10, "fit_flags": [],
        "title": "2018 Honda CB500X", "location": "Kingston", "url": "u", "year": 2018, "km": 16672,
    }  # fmt: skip

    html = _listing_row(listing)

    assert '<td class="num muted" data-v="2018">2018</td>' in html
    assert '<td class="num muted" data-v="16672">16,672</td>' in html
    per_year = round(5250 / (datetime.now().year - 2018))
    assert f'<td class="num muted" data-v="{per_year}">${per_year:,}</td>' in html


def test_row_shows_lazy_thumbnail_only_when_there_is_a_photo() -> None:
    listing = {
        "price": 5250, "rating": "GOOD", "delta_pct": -12.0, "fit": 5, "fit_flags": [],
        "title": "2018 Honda CB500X", "location": "Kingston", "url": "u",
        "image_url": "https://media.kijiji.ca/a.jpg?rule=kijijica-200-jpg&x=1",
    }  # fmt: skip

    assert (
        '<img class="thumb" src="https://media.kijiji.ca/a.jpg?rule=kijijica-200-jpg&amp;x=1"'
        ' alt="" loading="lazy">'
    ) in _listing_row(listing)
    assert "<img" not in _listing_row({**listing, "image_url": None})


def test_dealer_tag_and_private_median_label() -> None:
    listing = {
        "price": 7900, "rating": "HIGH", "delta_pct": 140.0, "fit": 5, "fit_flags": [],
        "title": "2024 Honda Rebel 500", "location": "Concord", "url": "u", "seller_type": "dealer",
    }  # fmt: skip
    stats = {"count": 5, "low": 3000, "median": 3200, "high": 3400, "sellers": "private"}

    html = _model_section(
        MarketSnapshot(slug="rebel500", stats=stats, listings=[listing], trend=None, prices=[])
    )

    assert '<span class="tag-dealer">dealer</span>' in html
    assert '<div class="label">Median (private)</div>' in html


def test_a_tab_lists_each_listing_once_cheapest_first(snap: MarketSnapshot) -> None:
    shared = snap.listings[0]
    other = MarketSnapshot(
        slug="crf230f",
        stats=snap.stats,
        listings=[shared, {**shared, "url": "https://x/cheap", "price": 300, "title": "Cheap 230"}],
        trend=None,
        prices=[],
    )
    street = MarketSnapshot(slug="rebel500", stats=snap.stats, listings=[], trend=None, prices=[])

    page = render_dashboard({"Dirt": [snap, other], "Street": [street]})

    table = page.split('id="all-dirt"')[1].split("</section>")[0]
    urls = {x["url"] for x in snap.listings} | {"https://x/cheap"}
    assert f"ALL DIRT BIKES ({len(urls)})" in page
    assert table.count("<tr>") - 1 == len(urls)  # the shared listing is one row
    assert "CRF150F, CRF230F" in table
    assert table.index("Cheap 230") < table.index(snap.listings[0]["title"][:20])
    assert "REBEL500" not in table
    assert 'class="sortable"' in page


def test_each_watchlist_is_a_tab_with_its_own_models(snap: MarketSnapshot) -> None:
    hoss = MarketSnapshot(slug="speedtwin", stats=snap.stats, listings=[], trend=None, prices=[])

    page = render_dashboard({"Dirt": [snap], "Hoss": [hoss]})

    assert (
        f'<button data-tab="dirt">Dirt ({len({x["url"] for x in snap.listings})})</button>' in page
    )
    assert '<button data-tab="hoss">Hoss (0)</button>' in page
    dirt, hoss_tab = page.split('<div class="tab" data-tab="hoss">')
    assert 'id="crf150f"' in dirt and 'id="speedtwin"' not in dirt
    assert 'id="speedtwin"' in hoss_tab and "ALL HOSS BIKES (0)" in hoss_tab


def test_new_at_dealers_lists_the_biggest_saving_first(snap: MarketSnapshot) -> None:
    def unit(title: str, price: float, was: float | None) -> dict:
        return {
            "model_slug": "crf150f", "title": title, "price": price, "was": was,
            "dealer": "Burlington Cycle", "url": f"https://x/{title}",
        }  # fmt: skip

    units = [
        unit("Full price", 4959, None),
        unit("Small cut", 4800, 4900),
        unit("Big cut", 4200, 4700),
    ]

    street = MarketSnapshot(slug="sv650", stats=snap.stats, listings=[], trend=None, prices=[])
    other_model = {**units[0], "model_slug": "sv650", "title": "Street unit"}

    page = render_dashboard({"Dirt": [snap], "Street": [street]}, new_units=[*units, other_model])

    section = page.split('id="new-dirt"')[1].split("</section>")[0]
    assert "NEW AT DEALERS (3)" in page
    assert 'href="#new-dirt"' in page
    assert "Street unit" not in section and 'id="new-street"' in page
    assert section.index("Big cut") < section.index("Small cut") < section.index("Full price")
    assert "$500" in section  # the saving


def test_no_new_units_no_section(snap: MarketSnapshot) -> None:
    assert 'id="new-dirt"' not in render_dashboard({"Dirt": [snap]})


def test_hidden_listings_sit_at_the_bottom_of_their_own_tab(snap: MarketSnapshot) -> None:
    snap.listings = [{**snap.listings[0], "id": 5}]
    snap.hidden = [
        {**snap.listings[0], "id": 6, "url": "https://x/hid", "title": "Parts bike",
         "hide_note": "too far", "hidden_at": "2026-10-19T10:00:00"}
    ]  # fmt: skip
    street = MarketSnapshot(slug="sv650", stats=snap.stats, listings=[], trend=None, prices=[])

    page = render_dashboard({"Dirt": [snap], "Street": [street]})

    dirt, street_tab = page.split('<div class="tab" data-tab="street">')
    assert '<button class="hide" data-id="5"' in dirt
    hidden = dirt.split('id="hidden-dirt"')[1]
    assert "HIDDEN (1)" in hidden
    assert "too far" in hidden
    assert 'class="unhide" data-id="6"' in hidden
    assert dirt.index('id="crf150f"') < dirt.index('id="hidden-dirt"')
    assert "Parts bike" not in dirt.split('id="hidden-dirt"')[0]
    assert "hidden-street" not in street_tab  # nothing hidden there: no section


def test_a_tab_filters_by_make(snap: MarketSnapshot) -> None:
    honda = replace(snap, make="Honda")
    yamaha = replace(
        snap, slug="ttr125", make="Yamaha",
        listings=[{**snap.listings[0], "url": "https://x/ttr", "title": "2010 TTR125"}],
    )  # fmt: skip
    hidden = {
        **snap.listings[0],
        "id": 9,
        "url": "https://x/h",
        "hide_note": None,
        "hidden_at": "2026-10-21",
    }

    page = render_dashboard({"Dirt": [honda, replace(yamaha, hidden=[hidden])]})

    assert '<button class="make active" data-make="">All</button>' in page
    assert page.index('data-make="honda">Honda<') < page.index('data-make="yamaha">Yamaha<')
    assert '<section class="model" id="ttr125" data-make="yamaha">' in page
    assert '<a href="#ttr125" data-make="yamaha">' in page
    table = page.split('id="all-dirt"')[1].split("</section>")[0]
    row = table.split("2010 TTR125")[0].rsplit("<tr", 1)[1]
    assert row.startswith(' data-make="yamaha">')
    assert '<tr data-make="yamaha">' in page.split('id="hidden-dirt"')[1]


def test_one_make_gets_no_filter(snap: MarketSnapshot) -> None:
    assert 'button class="make' not in render_dashboard({"Dirt": [replace(snap, make="Honda")]})


def test_all_bikes_table_puts_deals_first_and_shows_notes(snap: MarketSnapshot) -> None:
    base = {**snap.listings[0], "fit": 5, "fit_flags": []}
    rows = [
        {**base, "id": 1, "url": "u1", "title": "Cheap but high", "price": 500, "rating": "HIGH", "delta_pct": 30.0},
        {**base, "id": 2, "url": "u2", "title": "Good one", "price": 1800, "rating": "GOOD", "delta_pct": -15.0},
        {**base, "id": 3, "url": "u3", "title": "Big deal", "price": 1500, "rating": "DEAL", "delta_pct": -40.0, "notes": "asked VIN"},
        {**base, "id": 4, "url": "u4", "title": "Small deal", "price": 1200, "rating": "DEAL", "delta_pct": -25.0},
    ]  # fmt: skip

    page = render_dashboard({"Dirt": [replace(snap, listings=rows)]})

    table = page.split('id="all-dirt"')[1].split("</section>")[0]
    order = [table.index(t) for t in ("Big deal", "Small deal", "Good one", "Cheap but high")]
    assert order == sorted(order)
    assert '<td class="note" data-id="3" title="Click to edit">asked VIN</td>' in table
    assert '<td class="note" data-id="4" title="Click to edit"></td>' in table


def test_a_reload_keeps_the_scroll_position(snap: MarketSnapshot) -> None:
    page = render_dashboard({"Dirt": [snap]})

    assert 'history.scrollRestoration = "manual"' in page
    assert 'sessionStorage.setItem("scrollY"' in page
