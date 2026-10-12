"""
Generate a markdown market snapshot for a given model.

Output is suitable for pasting into a note, email, or chat message.
"""

from datetime import datetime
from pathlib import Path

from ..domain.scoring import MarketSnapshot


def render_report(snap: MarketSnapshot) -> str:
    """Build a markdown snapshot of the current market for a model."""
    model_slug, stats, listings, t = snap.slug, snap.stats, snap.listings, snap.trend
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append(f"# {model_slug.upper()} market snapshot")
    lines.append(f"*Generated {now}*")
    lines.append("")

    lines.append("## Market summary")
    if stats["count"] == 0:
        lines.append("No data. Scrape first.")
        return "\n".join(lines)

    lines.append(f"- Listings (90d): **{stats['count']}**")
    lines.append(f"- Low: **${int(stats['low'])}**")
    lines.append(f"- Median: **${int(stats['median'])}**")
    lines.append(f"- High: **${int(stats['high'])}**")
    if t is not None:
        arrow = "▼" if t < 0 else "▲"
        lines.append(f"- Trend (30d vs 30-90d): {arrow} {abs(t)}%")
    lines.append("")

    # Group by rating
    by_rating: dict[str, list[dict]] = {
        "DEAL": [],
        "GOOD": [],
        "FAIR": [],
        "HIGH": [],
        "UNKNOWN": [],
    }
    for listing in listings:
        by_rating.setdefault(listing["rating"], []).append(listing)

    lines.append("## Active listings")
    for rating in ("DEAL", "GOOD", "FAIR", "HIGH", "UNKNOWN"):
        bucket = by_rating.get(rating, [])
        if not bucket:
            continue
        lines.append(f"### {rating} ({len(bucket)})")
        for listing in bucket:
            price = (
                f"${int(listing['price'])}" if listing["price"] else listing.get("price_state", "?")
            )
            delta = f"({listing['delta_pct']:+.0f}%)" if listing["delta_pct"] is not None else ""
            loc = (listing["location"] or "").split(",")[0][:25]
            title = (listing["title"] or "")[:60]
            lines.append(f"- **{price}** {delta} - {loc} - [{title}]({listing['url']})")
        lines.append("")

    return "\n".join(lines)


def write_report(snap: MarketSnapshot, out_dir: Path = Path("reports")) -> Path:
    """Write the markdown report to disk and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.utcnow().strftime("%Y-%m-%d")
    path = out_dir / f"{snap.slug}_{date}.md"
    path.write_text(render_report(snap))
    return path
