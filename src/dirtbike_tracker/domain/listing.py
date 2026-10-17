"""A scraped listing, as every source adapter returns it. Pure data."""

from dataclasses import asdict, dataclass


@dataclass
class Listing:
    """One ad from any source (Kijiji, AutoTrader)."""

    source: str
    external_id: str
    url: str
    title: str
    price: float | None
    price_state: str  # numeric | please_contact | swap_trade | unknown
    location: str
    posted_text: str
    description: str
    model_slugs: list[str]  # one listing can match multiple models (e.g. a package)
    scraped_at: str
    full_description: str | None = None  # set when the search page already had the whole ad (#76)
    year: int | None = None
    km: int | None = None  # odometer; dirt bikes rarely have one
    image_url: str | None = None  # first photo, small (#10)
    seller_type: str | None = None  # "private" | "dealer" (#81); None when the site doesn't say

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class NewUnit:
    """A dealer's new unit of a tracked model, for the markdown watch (application/markdowns.py).

    `was` is the dealer's regular price or MSRP when the unit is listed below it."""

    dealer: str
    unit_id: str
    model_slug: str
    title: str
    price: float
    was: float | None
    url: str
    image_url: str | None = None
