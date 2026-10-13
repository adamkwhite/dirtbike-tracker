"""
Deal scoring: compute market medians and rate listings vs market.

A listing's deal rating is based on its price relative to the median
price of comparable listings. Pure: no I/O. Prices and listings are
fetched by infrastructure/storage.py and composed in application/market.py.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

# At or below this a "price" is a parts ad or placeholder, not a bike: it is
# excluded from the market AND never rated or alerted on.
MIN_BIKE_PRICE = 100

RATING_DEAL = -0.30
RATING_GOOD = -0.10
RATING_FAIR = 0.15

URGENT_MAX_DAYS = 2  # fresh DEAL: someone else will grab it
NEGOTIABLE_MIN_DAYS = 14  # stale GOOD: seller may take less


# Project fit (#21): is this a project bike to wrench on with the kids?
# Needing work is the point; red flags are what make the project not worth it.
FIT_NEUTRAL = 5
FIT_ALERT_MIN = 5  # below this, a DEAL/GOOD/target hit doesn't alert
FIT_RED_FLAGS = (
    "no ownership", "no title", "no papers", "lost ownership",
    "seized", "blown",
    "race bike", "race ready", "track bike",
)  # fmt: skip
NOT_RUNNING = (
    "doesn't run", "doesnt run", "does not run", "won't start", "wont start", "not running",
    "non running", "non-running", "non runner", "non-runner", "nonrunner",
)  # fmt: skip
# The bike's condition makes it a project: these define the project market segment (#72)
PROJECT_FLAGS = NOT_RUNNING + (
    "needs work", "needs tlc", "needs carb", "needs a carb", "carb clean",
    "needs rings", "needs a top end", "needs a rebuild", "timing chain",
    "project", "barn find", "mechanic special", "sat for",
)  # fmt: skip
FIT_GREEN_FLAGS = PROJECT_FLAGS + (
    "ownership", "one owner", "trail", "my son", "my daughter", "my kids",
)  # fmt: skip
# Matched and ignored, so they can't trip a green flag ("not a project, ready to ride")
FIT_IGNORE = ("not a project", "no project")

# goal: ride (models.yml): a bike to ride as-is, e.g. a street bike for spring.
# Needing work is now a red flag, and an Ontario safety certificate is a green one.
RIDE_RED_FLAGS = FIT_RED_FLAGS + PROJECT_FLAGS + (
    # "as is" in an Ontario ad means no safety certificate: not ready to ride
    "no safety", "without safety", "not safetied", "not certified", "as is", "as-is",
)  # fmt: skip
RIDE_GREEN_FLAGS = (
    "runs great", "runs well", "runs good", "runs perfect", "runs and rides",
    "ownership", "one owner", "safety", "safetied", "certified",
    "low km", "low kms", "serviced", "new tires", "new tire", "winter stored", "stored inside",
)  # fmt: skip
GOALS = ("project", "ride")


def _flags_re(flags: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(map(re.escape, flags)) + r")\b")


_FLAG_RES = {
    "project": (_flags_re(FIT_RED_FLAGS), _flags_re(FIT_GREEN_FLAGS)),
    "ride": (_flags_re(RIDE_RED_FLAGS), _flags_re(RIDE_GREEN_FLAGS)),
}
_IGNORE_RE = _flags_re(FIT_IGNORE)


def project_fit(text: str, goal: str = "project") -> tuple[int, list[str]]:
    """0-10 fit for the model's goal from red/green phrases, plus the phrases hit ('-seized', '+project').

    Red and ignored phrases are removed before green matching, so "no ownership"
    never also counts as "ownership".
    """
    red_re, green_re = _FLAG_RES[goal]
    text = _IGNORE_RE.sub(" ", text.lower().replace("\u2019", "'"))
    reds = sorted(set(red_re.findall(text)))
    greens = sorted(set(green_re.findall(red_re.sub(" ", text))))
    score = FIT_NEUTRAL + 2 * len(greens) - 3 * len(reds)
    return max(0, min(10, score)), [f"-{r}" for r in reds] + [f"+{g}" for g in greens]


# Project bikes are rated against the project median once it has this many prices
PROJECT_MIN_SAMPLES = 5


def is_project(fit_flags: list[str]) -> bool:
    """A project bike by condition (doesn't run, needs work, ...), per project_fit's flags."""
    return any(flag[1:] in PROJECT_FLAGS for flag in fit_flags if flag.startswith("+"))


@dataclass
class MarketSnapshot:
    """One model's scored market, as consumed by the CLI and renderers."""

    slug: str
    stats: dict
    listings: list[dict]
    trend: float | None
    prices: list[float]
    sold: list[dict] = field(default_factory=list)  # likely-sold, most recent first
    weekly: list[tuple[str, float | None]] = field(default_factory=list)  # (week start, median)


def market_stats(prices: list[float]) -> dict:
    """Return low/median/high/count for a set of market prices."""
    if not prices:
        return {"low": None, "median": None, "high": None, "count": 0}
    return {
        "low": min(prices),
        "median": median(prices),
        "high": max(prices),
        "count": len(prices),
    }


VS_LABELS = {"project": "vs project median", "fallback": "vs all bikes*"}
FALLBACK_NOTE = "* project bike, but too few project prices yet for a project median"


def vs_label(listing: dict) -> str:
    """What the listing's delta_pct is relative to."""
    return VS_LABELS.get(listing.get("basis") or "", "vs median")


# A model's market is its private sales once it has this many in the window (#81): dealer
# prices run high, and a dealer cross-posted to both sites counts twice.
PRIVATE_MIN_SAMPLES = 5


def private_market(market: list[dict]) -> tuple[list[dict], str]:
    """(rows to rate against, "private" | "all"). Unknown seller type isn't counted as private."""
    private = [x for x in market if x.get("seller_type") == "private"]
    return (private, "private") if len(private) >= PRIVATE_MIN_SAMPLES else (market, "all")


def is_dealer_new(seller_type: str | None, year: int | None, this_year: int) -> bool:
    """A dealer's current-or-next-model-year bike is new or demo stock, even when listed as used.

    Sites don't always mark it (Kijiji vehicletype, AutoTrader offerType) and MSRP skews the median.
    """
    return seller_type == "dealer" and year is not None and year >= this_year


# Sellers who skip Kijiji's km field leave 0 or 1; nothing this low is a real reading
MIN_REAL_KM = 10


def parse_km(value: object) -> int | None:
    """Odometer km from 16672, "16672" or "25,538 km"; None when missing or a placeholder."""
    digits = "".join(c for c in str(value or "") if c.isdigit())
    km = int(digits) if digits else 0
    return km if km >= MIN_REAL_KM else None


def parse_year(value: object, this_year: int) -> int | None:
    """Model year, or None when missing or implausible."""
    text = str(value or "")
    year = int(text) if text.isdigit() else 0
    return year if 1970 <= year <= this_year + 1 else None


def specs_label(listing: dict) -> str:
    """'2018 · 16,672 km · dealer', whichever parts are known (private sellers aren't labelled)."""
    parts = [str(listing["year"])] if listing.get("year") else []
    if listing.get("km"):
        parts.append(f"{listing['km']:,} km")
    if listing.get("seller_type") == "dealer":
        parts.append("dealer")
    return " · ".join(parts)


def price_per_year(listing: dict, this_year: int) -> int | None:
    """Price per year of age (#16): a 2015 at $2500 in 2026 is $227/yr. None if unknown."""
    if not listing.get("price") or not listing.get("year"):
        return None
    return round(float(listing["price"]) / max(1, this_year - int(listing["year"])))


def median_label(stats: dict) -> str:
    """'Median (private)' when the market is private sales only (#81), else 'Median'."""
    return "Median (private)" if stats.get("sellers") == "private" else "Median"


def project_line(stats: dict) -> str | None:
    """'Project median $1400 (6 bikes)', or None when there are too few project prices."""
    if stats.get("project_median") is None:
        return None
    return f"Project median ${int(stats['project_median'])} ({stats['project_count']} bikes)"


def project_stats(market: list[dict]) -> dict:
    """Median of the project bikes in a market ({"price", "title", "description"} rows).

    project_median is None below PROJECT_MIN_SAMPLES: too few to rate against.
    """
    prices = [
        x["price"]
        for x in market
        if is_project(project_fit(f"{x['title'] or ''} {x['description'] or ''}")[1])
    ]
    enough = len(prices) >= PROJECT_MIN_SAMPLES
    return {"project_median": median(prices) if enough else None, "project_count": len(prices)}


def rate_listing(price: float | None, median_price: float | None) -> dict:
    """Rate one listing's price against the market median."""
    if price is None or price <= MIN_BIKE_PRICE or not median_price:
        return {"rating": "UNKNOWN", "delta_pct": None}

    delta = (price - median_price) / median_price
    if delta <= RATING_DEAL:
        rating = "DEAL"
    elif delta <= RATING_GOOD:
        rating = "GOOD"
    elif delta <= RATING_FAIR:
        rating = "FAIR"
    else:
        rating = "HIGH"

    return {"rating": rating, "delta_pct": round(delta * 100, 1)}


def trend(recent: list[float], older: list[float]) -> float | None:
    """Percent change of the last-30-day median vs the 30-90-day median."""
    if not recent or not older:
        return None
    return round((median(recent) - median(older)) / median(older) * 100, 1)


def urgency(rating: str, age_days: float | None) -> str | None:
    """URGENT for a fresh DEAL, NEGOTIABLE for a stale GOOD, else None."""
    if age_days is None:
        return None
    if rating == "DEAL" and age_days < URGENT_MAX_DAYS:
        return "URGENT"
    if rating == "GOOD" and age_days > NEGOTIABLE_MIN_DAYS:
        return "NEGOTIABLE"
    return None


def under_target(listing: dict, target_price: float | None) -> bool:
    """Priced at or below the model's target. An unrated price (parts ad, placeholder) never is."""
    return (
        target_price is not None
        and listing["rating"] != "UNKNOWN"
        and listing["price"] <= target_price
    )


def needs_detail(listing: dict, target_price: float | None) -> bool:
    """Worth fetching the full ad: it could alert, and we haven't read its body yet."""
    if listing.get("detail_fetched_at"):
        return False
    return listing["rating"] in ("DEAL", "GOOD") or under_target(listing, target_price)


def score_listings(
    listings: list[dict],
    median_price: float | None,
    project_median: float | None = None,
    goal: str = "project",
) -> list[dict]:
    """Attach rating, urgency and project fit to each listing, best deals first, unpriced last.

    A project bike is rated against project_median when there is one (basis "project");
    without it, against the whole market and marked as such (basis "fallback"), since a
    non-runner looks like a DEAL next to running bikes just for not running.
    A "ride" goal has no project segment: needing work only lowers fit.
    """
    scored = []
    for listing in listings:
        text = f"{listing.get('title') or ''} {listing.get('description') or ''}"
        fit, fit_flags = project_fit(text, goal)
        basis = None
        if goal == "project" and is_project(fit_flags):
            basis = "project" if project_median else "fallback"
        median_for = project_median if basis == "project" else median_price
        rated = {**listing, **rate_listing(listing["price"], median_for)}
        rated["urgency"] = urgency(rated["rating"], listing.get("age_days"))
        rated["fit"], rated["fit_flags"], rated["basis"] = fit, fit_flags, basis
        scored.append(rated)
    scored.sort(key=lambda x: (x["delta_pct"] is None, x["delta_pct"] or 0))
    return scored


def weekly_medians(
    timelines: list[dict], now: datetime, weeks: int = 13
) -> list[tuple[str, float | None]]:
    """Market median for each of the last `weeks` 7-day windows, oldest first.

    timelines: {"start", "end": datetime, "history": [(datetime, price), ...] ascending}.
    A listing counts in a window if it was on the market at any point in it, at its
    latest price by the window's end. Windows with no priced listings are None.
    """
    out: list[tuple[str, float | None]] = []
    for i in range(weeks - 1, -1, -1):
        w_end = now - timedelta(days=7 * i)
        w_start = w_end - timedelta(days=7)
        prices = [p for t in timelines if (p := _price_in_window(t, w_start, w_end))]
        out.append((w_start.date().isoformat(), median(prices) if prices else None))
    return out


def _price_in_window(t: dict, w_start: datetime, w_end: datetime) -> float | None:
    """A listing's price as of w_end if it was on the market in the window, else None."""
    if t["start"] > w_end or t["end"] < w_start or not t["history"]:
        return None
    known = [p for at, p in t["history"] if at <= w_end]
    # ponytail: before our first observation, assume the first price we saw.
    # Kijiji post dates reach back months; without this the chart starts empty.
    price = known[-1] if known else t["history"][0][1]
    return price if price and price > MIN_BIKE_PRICE else None
