"""Full-ad enrichment (#71) against a real SQLite DB, with a fake fetcher as the port."""

from pathlib import Path

import pytest

from dirtbike_tracker.application import details
from dirtbike_tracker.application.cli import _alert_for
from dirtbike_tracker.application.details import fetch_details
from dirtbike_tracker.application.market import snapshot
from dirtbike_tracker.infrastructure.kijiji_scraper import Listing
from dirtbike_tracker.infrastructure.storage import connect, init_db, upsert_listings


def _listing(ext_id: str, price: float, description: str = "") -> Listing:
    return Listing(
        source="kijiji",
        external_id=ext_id,
        url=f"https://www.kijiji.ca/v-dirt-bikes-motocross/x/{ext_id}",
        title=f"Honda CRF150F {ext_id}",
        price=price,
        price_state="numeric",
        location="Barrie, ON",
        posted_text="",
        description=description,
        model_slugs=["crf150f"],
        scraped_at="2026-10-12T10:00:00",
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "listings.db"
    init_db(path)
    # median 2000: "deal" at 1200 is a DEAL, the rest FAIR
    upsert_listings(
        [_listing("deal", 1200), _listing("mid", 2000), _listing("high", 2100)], db_path=path
    )
    return path


def _by_id(db: Path) -> dict[str, dict]:
    return {x["external_id"]: x for x in snapshot("crf150f", db_path=db).listings}


def test_red_flag_in_ad_body_stops_the_alert(db: Path) -> None:
    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url.rsplit("/", 1)[-1])
        return "Runs, but no ownership. Cash only."

    assert _alert_for(_by_id(db)["deal"], None) is not None  # snippet alone: alerts

    saved = fetch_details(
        snapshot("crf150f", db_path=db).listings, None, {"kijiji": fake_fetch}, db
    )
    deal = _by_id(db)["deal"]

    assert (saved, fetched) == (1, ["deal"])  # FAIR listings aren't worth a fetch
    assert deal["fit"] == 2
    assert _alert_for(deal, None) is None


def test_project_phrase_in_ad_body_raises_fit(db: Path) -> None:
    fetch_details(
        snapshot("crf150f", db_path=db).listings,
        None,
        {"kijiji": lambda _: "Doesn't run, needs carb clean. Have ownership."},
        db,
    )

    assert _by_id(db)["deal"]["fit"] == 10


def test_full_description_survives_rescrape_and_is_fetched_once(db: Path) -> None:
    fetch_details(
        snapshot("crf150f", db_path=db).listings, None, {"kijiji": lambda _: "project"}, db
    )
    upsert_listings([_listing("deal", 1200, description="card snippet")], db_path=db)

    listings = snapshot("crf150f", db_path=db).listings

    assert _by_id(db)["deal"]["description"] == "project"
    assert fetch_details(listings, None, {"kijiji": lambda _: pytest.fail("refetched")}, db) == 0


def test_failed_fetch_saves_nothing_and_retries(db: Path) -> None:
    listings = snapshot("crf150f", db_path=db).listings

    assert fetch_details(listings, None, {"kijiji": lambda _: None}, db) == 0
    assert fetch_details(listings, None, {"kijiji": lambda _: "ok"}, db) == 1


def test_target_price_widens_and_budget_caps_fetches(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(details, "DETAIL_BUDGET", 2)
    listings = snapshot("crf150f", db_path=db).listings
    fetched: list[str] = []

    def fake_fetch(url: str) -> str:
        fetched.append(url)
        return ""

    # target 2500: all three qualify, budget allows two; no adapter for a source = skipped
    assert fetch_details(listings, 2500, {"kijiji": fake_fetch}, db) == 2
    assert fetch_details(listings, 2500, {"autotrader": fake_fetch}, db) == 0
    assert len(fetched) == 2


def test_init_db_migrates_an_old_schema_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with connect(path) as conn:
        conn.execute(
            "CREATE TABLE listings (id INTEGER PRIMARY KEY, source TEXT, external_id TEXT,"
            " url TEXT, title TEXT, price REAL, location TEXT, posted_text TEXT,"
            " description TEXT, first_seen TEXT, last_seen TEXT, is_active INTEGER)"
        )
        conn.execute("INSERT INTO listings (source, external_id) VALUES ('kijiji', '1')")

    init_db(path)

    with connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
        rows = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    assert {"full_description", "detail_fetched_at", "dismissed_at", "sold_at"} <= cols
    assert rows == 1


def test_complete_search_snippet_skips_the_detail_fetch(db: Path) -> None:
    whole = _listing("deal", 1200, description="Doesn't run, needs carb clean. Have ownership.")
    whole.full_description = whole.description
    upsert_listings([whole], db_path=db)

    listings = snapshot("crf150f", db_path=db).listings

    assert fetch_details(listings, None, {"kijiji": lambda _: pytest.fail("fetched")}, db) == 0
    assert _by_id(db)["deal"]["fit"] == 10


def test_cut_off_snippet_updates_description_and_still_fetches(db: Path) -> None:
    upsert_listings([_listing("deal", 1200, description="Runs, needs carb ...")], db_path=db)

    listings = snapshot("crf150f", db_path=db).listings

    assert _by_id(db)["deal"]["description"] == "Runs, needs carb ..."
    assert fetch_details(listings, None, {"kijiji": lambda _: "full"}, db) == 1


def test_search_snippet_never_overwrites_a_fetched_body(db: Path) -> None:
    fetch_details(
        snapshot("crf150f", db_path=db).listings, None, {"kijiji": lambda _: "fetched body"}, db
    )
    short = _listing("deal", 1200, description="short")
    short.full_description = "short"
    upsert_listings([short], db_path=db)

    assert _by_id(db)["deal"]["description"] == "fetched body"
