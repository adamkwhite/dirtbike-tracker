"""Ports: what the application needs from the outside world (#74).

Adapters live in infrastructure/; cli.py, the composition root, builds them
into an App. Nothing else in application/ imports infrastructure, so tests
pass fakes in instead of patching modules.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ..domain.listing import Listing


class ListingSource(Protocol):
    """Search one site for a model. `query` is the search text when it isn't the slug;
    `include_new` keeps dealer new stock (models.yml). Only passed when a model sets them."""

    def __call__(
        self,
        model_slug: str,
        region: str,
        max_pages: int,
        /,
        *,
        query: str | None = None,
        include_new: bool = False,
    ) -> list[Listing]: ...


AdDetailFetcher = Callable[[str], str | None]  # ad url -> description, None if the fetch failed


class Notifier(Protocol):
    """Send one push. True only if it went out."""

    def __call__(
        self,
        title: str,
        body: str,
        priority: int = 3,
        tags: list[str] | None = None,
        click_url: str | None = None,
        attach_url: str | None = None,
    ) -> bool: ...


class ListingStore(Protocol):
    """Listing persistence, as the application uses it. SQLite is the only adapter."""

    def upsert_listings(self, listings: Iterable[Listing]) -> dict: ...
    def mark_stale(self, sources: Iterable[str]) -> int: ...
    def active_listings(self, model_slug: str) -> list[dict]: ...
    def market_listings(
        self, model_slug: str, days: int = 90, older_than_days: int = 0
    ) -> list[dict]: ...
    def price_drops(self, model_slug: str) -> dict[int, float]: ...
    def price_timelines(self, model_slug: str) -> list[dict]: ...
    def sold_listings(self, model_slug: str) -> list[dict]: ...
    def save_full_description(self, listing_id: int, text: str) -> None: ...
    def set_dismissed(self, ref: str, dismissed: bool) -> list[str]: ...
    def set_contact(self, ref: str, at: datetime | None, note: str | None = None) -> list[str]: ...


@dataclass
class App:
    """Everything a command needs, wired once at the entry point."""

    store: ListingStore
    sources: Mapping[str, ListingSource]
    detail_fetchers: Mapping[str, AdDetailFetcher]
    notify: Notifier
    config: dict  # models.yml
    state_dir: Path  # notified ids, source health
    dashboard_path: Path
    reports_dir: Path
    ping: Callable[[], bool]  # dead-man's switch after a completed watch
