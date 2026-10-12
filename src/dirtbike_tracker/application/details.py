"""Read the full ad of listings that could alert (#71).

AdDetailFetcher is a port: this module decides which listings and how many;
an adapter per source only turns an ad URL into its description.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

from ..domain.scoring import needs_detail
from ..infrastructure.storage import DB_PATH, save_full_description

AdDetailFetcher = Callable[[str], str | None]  # ad url -> description, None if the fetch failed

# Per model per run. Each fetch sleeps ~3s (scraper rate limit), so this caps a run's extra time.
DETAIL_BUDGET = 5


def fetch_details(
    listings: list[dict],
    target_price: float | None,
    fetchers: Mapping[str, AdDetailFetcher],
    db_path: Path = DB_PATH,
) -> int:
    """Fetch and store full descriptions for listings that need one. Returns how many were saved.

    A failed fetch saves nothing, so the listing is retried next run.
    """
    wanted = [x for x in listings if x["source"] in fetchers and needs_detail(x, target_price)]
    saved = 0
    for listing in wanted[:DETAIL_BUDGET]:
        text = fetchers[listing["source"]](listing["url"])
        if text is not None:
            save_full_description(listing["id"], text, db_path=db_path)
            saved += 1
    return saved
