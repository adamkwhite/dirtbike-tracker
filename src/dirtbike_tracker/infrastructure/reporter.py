"""
Generate a markdown market snapshot for a given model.

Output is suitable for pasting into a note, email, or chat message.
"""

from datetime import UTC, datetime
from pathlib import Path

from ..domain.scoring import MarketSnapshot, project_line, vs_label


def location_label(listing: dict, width: int) -> str:
    """'Barrie (14d)': city plus days on market (from the source post date when known)."""
    loc = (listing.get("location") or "").split(",")[0][:width]
    age = listing.get("age_days")
    return f"{loc} ({int(age)}d)" if age is not None else loc


RATING_ORDER = ("DEAL", "GOOD", "FAIR", "HIGH", "UNKNOWN")


def _summary_lines(stats: dict, trend: float | None) -> list[str]:
    lines = [
        f"- Listings (90d): **{stats['count']}**",
        f"- Low: **${int(stats['low'])}**",
        f"- Median: **${int(stats['median'])}**",
        f"- High: **${int(stats['high'])}**",
    ]
    if line := project_line(stats):
        lines.append(f"- {line}")
    if trend is not None:
        arrow = "▼" if trend < 0 else "▲"
        lines.append(f"- Trend (30d vs 30-90d): {arrow} {abs(trend)}%")
    return lines


def _listing_line(listing: dict) -> str:
    price = f"${int(listing['price'])}" if listing["price"] else listing.get("price_state", "?")
    delta = (
        f"({listing['delta_pct']:+.0f}% {vs_label(listing)})"
        if listing["delta_pct"] is not None
        else ""
    )
    title = (listing["title"] or "")[:60]
    return f"- **{price}** {delta} - {location_label(listing, 25)} - [{title}]({listing['url']})"


def render_report(snap: MarketSnapshot) -> str:
    """Build a markdown snapshot of the current market for a model."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# {snap.slug.upper()} market snapshot",
        f"*Generated {now}*",
        "",
        "## Market summary",
    ]
    if snap.stats["count"] == 0:
        lines.append("No data. Scrape first.")
        return "\n".join(lines)

    lines += [*_summary_lines(snap.stats, snap.trend), "", "## Active listings"]
    for rating in RATING_ORDER:
        bucket = [x for x in snap.listings if x["rating"] == rating]
        if bucket:
            lines += [f"### {rating} ({len(bucket)})", *map(_listing_line, bucket), ""]

    return "\n".join(lines)


def write_report(snap: MarketSnapshot, out_dir: Path = Path("reports")) -> Path:
    """Write the markdown report to disk and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = out_dir / f"{snap.slug}_{date}.md"
    path.write_text(render_report(snap))
    return path
