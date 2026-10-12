"""Read the full ad of listings that could alert (#71).

This module decides which listings and how many; an AdDetailFetcher adapter
per source (ports.py) only turns an ad URL into its description.
"""

from collections.abc import Mapping

from ..domain.scoring import needs_detail
from .ports import AdDetailFetcher, ListingStore

# Per model per run. Each fetch sleeps ~3s (scraper rate limit), so this caps a run's extra time.
DETAIL_BUDGET = 5


def fetch_details(
    listings: list[dict],
    target_price: float | None,
    fetchers: Mapping[str, AdDetailFetcher],
    store: ListingStore,
) -> int:
    """Fetch and store full descriptions for listings that need one. Returns how many were saved.

    A failed fetch saves nothing, so the listing is retried next run.
    """
    wanted = [x for x in listings if x["source"] in fetchers and needs_detail(x, target_price)]
    saved = 0
    for listing in wanted[:DETAIL_BUDGET]:
        text = fetchers[listing["source"]](listing["url"])
        if text is not None:
            store.save_full_description(listing["id"], text)
            saved += 1
    return saved
