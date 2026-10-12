"""Record real Kijiji / AutoTrader pages for the drift tests (#28).

Saves to tests/recorded/, which is gitignored: the pages are third-party ads
with private sellers' names and numbers, and this repo is public. Re-run, then
`pytest tests/drift`, to check our parsers still read the live sites.

Usage: python scripts/record_pages.py   (or: make drift)
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from dirtbike_tracker.infrastructure import autotrader_scraper, kijiji_scraper

OUT = Path(__file__).resolve().parent.parent / "tests" / "recorded"

# A kid dirt bike (cards, snippets) and a street bike (year/km form fields, dealer stock)
KIJIJI_SLUGS = ("crf150f", "rebel500")
AUTOTRADER_SLUG = "rebel500"  # its results include new dealer stock (offerType N)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pages: dict[str, dict] = {}

    for slug in KIJIJI_SLUGS:
        url = kijiji_scraper.build_search_url(slug)
        name = f"kijiji_search_{slug}.html"
        (OUT / name).write_text(kijiji_scraper.fetch_page(url))
        pages[name] = {"url": url, "slug": slug}

    # one ad page, for the full description (#71): the first listing on the first search
    first = kijiji_scraper.parse_listings(
        (OUT / f"kijiji_search_{KIJIJI_SLUGS[0]}.html").read_text(), KIJIJI_SLUGS[0]
    )
    if first:
        ad = first[0]
        (OUT / "kijiji_ad.html").write_text(kijiji_scraper.fetch_page(ad.url))
        pages["kijiji_ad.html"] = {"url": ad.url, "external_id": ad.external_id}

    url = autotrader_scraper.build_search_url(AUTOTRADER_SLUG)
    (OUT / "autotrader_search.html").write_text(autotrader_scraper.fetch_page(url))
    pages["autotrader_search.html"] = {"url": url, "slug": AUTOTRADER_SLUG}

    manifest = {"recorded_at": datetime.now(UTC).isoformat(timespec="seconds"), "pages": pages}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    for name in pages:
        print(f"{name}: {(OUT / name).stat().st_size:,} bytes")  # noqa: T201


if __name__ == "__main__":
    main()
