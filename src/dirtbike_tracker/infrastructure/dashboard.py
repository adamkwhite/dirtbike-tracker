"""
Static HTML dashboard generator.

Writes a single self-contained HTML file (plus one Chart.js from CDN)
showing all tracked models at a glance: active listings, deal ratings,
market stats, and a price distribution chart per model.

Run via `python src/main.py dashboard` or let cron do it every 20 min.
"""

import html
import json
from datetime import datetime
from pathlib import Path

from ..domain.scoring import FALLBACK_NOTE, MarketSnapshot, price_per_year, project_line, vs_label
from .reporter import contact_label, location_label

RATING_HEX = {
    "DEAL": "#10b981",  # emerald
    "GOOD": "#06b6d4",  # cyan
    "FAIR": "#eab308",  # yellow
    "HIGH": "#ef4444",  # red
    "UNKNOWN": "#6b7280",  # gray
}


def _esc(text) -> str:
    return html.escape(str(text) if text is not None else "")


def _histogram(prices: list[float], bins: int = 10) -> tuple[list[float], list[int]]:
    """Return (bin_centers, counts) for a price histogram."""
    if not prices:
        return [], []
    lo, hi = min(prices), max(prices)
    if lo == hi:
        return [lo], [len(prices)]
    width = (hi - lo) / bins
    edges = [lo + i * width for i in range(bins + 1)]
    counts = [0] * bins
    for p in prices:
        idx = min(int((p - lo) / width), bins - 1)
        counts[idx] += 1
    centers = [edges[i] + width / 2 for i in range(bins)]
    return centers, counts


def _tiles(stats: dict, t: float | None) -> str:
    if stats["count"] == 0:
        return '<div class="tile tile-empty">No listings yet</div>'
    trend_html = ""
    if t is not None:
        arrow = "▼" if t < 0 else "▲"
        trend_class = "trend-down" if t < 0 else "trend-up"
        trend_html = f'<div class="tile"><div class="label">Trend 30d</div><div class="val {trend_class}">{arrow} {abs(t):.0f}%</div></div>'
    project_html = ""
    if stats.get("project_median") is not None:
        project_html = f'<div class="tile" title="{_esc(project_line(stats) or "")}"><div class="label">Project median</div><div class="val">${int(stats["project_median"])}</div></div>'
    return f"""
          <div class="tile"><div class="label">Count</div><div class="val">{stats["count"]}</div></div>
          <div class="tile"><div class="label">Low</div><div class="val">${int(stats["low"])}</div></div>
          <div class="tile tile-median"><div class="label">Median</div><div class="val">${int(stats["median"])}</div></div>
          <div class="tile"><div class="label">High</div><div class="val">${int(stats["high"])}</div></div>
          {project_html}
          {trend_html}
        """


def _listing_row(listing: dict) -> str:
    rating = listing["rating"]
    delta = listing["delta_pct"]
    delta_str = f"{delta:+.0f}%" if delta is not None else "—"
    vs = vs_label(listing)
    if listing.get("basis") == "fallback":
        delta_str += "*"
        vs += f" ({FALLBACK_NOTE.lstrip('* ')})"
    if listing["price"]:
        price_str = f"${int(listing['price'])}"
    else:
        price_str = {"please_contact": "Contact", "swap_trade": "Trade only"}.get(
            listing.get("price_state", ""), "?"
        )
    loc = location_label(listing, 28)
    title = (listing["title"] or "")[:65]
    per_year = price_per_year(listing, datetime.now().year)
    thumb = ""
    if listing.get("image_url"):
        thumb = (
            f'<a href="{_esc(listing["url"])}" target="_blank" rel="noopener">'
            f'<img class="thumb" src="{_esc(listing["image_url"])}" alt="" loading="lazy"></a>'
        )
    contacted = ""
    if label := contact_label(listing):
        days = label.split(":")[0]
        contacted = f' <span class="contacted" title="{_esc(label)}">{_esc(days)}</span>'
    return f"""
              <tr>
                <td><span class="badge" style="background:{RATING_HEX.get(rating, "#6b7280")}">{_esc(rating)}</span></td>
                <td class="num">{_esc(price_str)}</td>
                <td class="num muted">{listing.get("year") or "—"}</td>
                <td class="num muted">{f"{listing['km']:,}" if listing.get("km") else "—"}</td>
                <td class="num muted">{f"${per_year:,}" if per_year else "—"}</td>
                <td class="num muted" title="{_esc(vs)}">{_esc(delta_str)}</td>
                <td class="num" title="{_esc(", ".join(listing["fit_flags"]) or "no flags")}">{listing["fit"]}</td>
                <td class="muted">{_esc(loc)}</td>
                <td>{thumb}<a href="{_esc(listing["url"])}" target="_blank" rel="noopener">{_esc(title)}</a>{contacted}</td>
              </tr>"""


def _sold_table(sold: list[dict]) -> str:
    """Likely-sold history: what bikes actually went for, and how fast."""
    if not sold:
        return ""
    sold_rows = "".join(
        f"""
              <tr>
                <td class="num">{_esc(f"${int(s['price'])}" if s["price"] else "?")}</td>
                <td class="num muted">{_esc(f"{s['days_listed']}d")}</td>
                <td class="muted">{_esc((s["location"] or "").split(",")[0][:28])}</td>
                <td class="muted">{_esc(s["sold_at"][:10])}</td>
                <td><a href="{_esc(s["url"])}" target="_blank" rel="noopener">{_esc((s["title"] or "")[:65])}</a></td>
              </tr>"""
        for s in sold
    )
    return f"""
      <h3>Recently sold (likely)</h3>
      <table>
        <thead>
          <tr><th>Price</th><th>Listed</th><th>Location</th><th>Gone</th><th>Title</th></tr>
        </thead>
        <tbody>{sold_rows}</tbody>
      </table>"""


def _model_section(snap: MarketSnapshot) -> str:
    """Render one model's panel."""
    slug, listings, prices = snap.slug, snap.listings, snap.prices
    tiles = _tiles(snap.stats, snap.trend)
    rows_html = (
        "".join(map(_listing_row, listings[:20]))
        or '<tr><td colspan="6" class="muted">No active listings</td></tr>'
    )
    sold_html = _sold_table(snap.sold)

    # Weekly market median (line). Needs 2+ weeks with data to say anything.
    trend_id = f"trend-{slug}"
    weekly = [(week, val) for week, val in snap.weekly]
    if sum(val is not None for _, val in weekly) >= 2:
        weekly_html = f'<div class="chart-wrap"><canvas id="{trend_id}" height="50"></canvas></div>'
    else:
        weekly_html = '<p class="muted">Weekly median chart appears once 2+ weeks have prices.</p>'
    weekly_data = json.dumps(
        {"labels": [week[5:] for week, _ in weekly], "data": [val for _, val in weekly]}
    )

    # Histogram data for Chart.js
    centers, counts = _histogram(prices, bins=10)
    chart_id = f"chart-{slug}"
    chart_data = json.dumps(
        {
            "labels": [f"${int(c)}" for c in centers],
            "data": counts,
        }
    )

    return f"""
    <section class="model" id="{slug}">
      <h2>{slug.upper()}</h2>
      <div class="tiles">{tiles}</div>
      <h3>Median by week</h3>
      {weekly_html}
      <h3>Price distribution (90d)</h3>
      <div class="chart-wrap"><canvas id="{chart_id}" height="80"></canvas></div>
      <table>
        <thead>
          <tr><th>Rating</th><th>Price</th><th>Year</th><th>km</th><th title="Price per year of age">$/yr</th><th>vs Med</th><th title="Project fit 0-10: red/green phrases in the ad">Fit</th><th>Location</th><th>Title</th></tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>{sold_html}
      <script>
        (function() {{
          const el = document.getElementById('{trend_id}');
          if (!el) return;
          const weekly = {weekly_data};
          new Chart(el, {{
            type: 'line',
            data: {{
              labels: weekly.labels,
              datasets: [{{
                data: weekly.data,
                borderColor: '#06b6d4',
                pointRadius: 2,
                spanGaps: true,
                tension: 0.2,
              }}],
            }},
            options: {{
              plugins: {{ legend: {{ display: false }} }},
              scales: {{
                x: {{ grid: {{ display: false }}, ticks: {{ color: '#9ca3af', font: {{ size: 10 }} }} }},
                y: {{ grid: {{ color: '#1f2937' }}, ticks: {{ color: '#9ca3af', callback: (v) => '$' + v }} }},
              }},
            }},
          }});
        }})();
        (function() {{
          const data = {chart_data};
          if (data.data.length === 0) return;
          new Chart(document.getElementById('{chart_id}'), {{
            type: 'bar',
            data: {{
              labels: data.labels,
              datasets: [{{
                data: data.data,
                backgroundColor: '#06b6d4',
                borderRadius: 2,
              }}],
            }},
            options: {{
              plugins: {{ legend: {{ display: false }} }},
              scales: {{
                x: {{ grid: {{ display: false }}, ticks: {{ color: '#9ca3af', font: {{ size: 10 }} }} }},
                y: {{ grid: {{ color: '#1f2937' }}, ticks: {{ color: '#9ca3af', precision: 0 }} }},
              }},
            }},
          }});
        }})();
      </script>
    </section>
    """


def render_dashboard(snaps: list[MarketSnapshot]) -> str:
    """Build the full HTML page."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sections = "\n".join(_model_section(s) for s in snaps)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dirt Bike Tracker</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏍️</text></svg>">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0b1120;
      --panel: #111827;
      --border: #1f2937;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #06b6d4;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-padding-top: 60px; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    header {{
      padding: 24px 32px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: baseline;
    }}
    header h1 {{ margin: 0; font-size: 24px; font-weight: 600; }}
    header .meta {{ color: var(--muted); font-size: 13px; }}
    .nav {{
      padding: 12px 32px;
      border-bottom: 1px solid var(--border);
      display: flex;
      position: sticky;
      top: 0;
      background: var(--bg);
      z-index: 10;
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      gap: 16px;
      flex-wrap: wrap;
    }}
    .nav a {{
      color: var(--muted);
      text-decoration: none;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .nav a:hover {{ color: var(--accent); }}
    main {{ padding: 24px 32px; max-width: 1400px; margin: 0 auto; }}
    .model {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px 24px;
      margin-bottom: 24px;
    }}
    .model h2 {{
      margin: 0 0 16px 0;
      font-size: 18px;
      font-weight: 600;
      letter-spacing: 0.5px;
    }}
    .tiles {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
      gap: 8px;
      margin-bottom: 16px;
    }}
    .tile {{
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 12px;
    }}
    .tile-median {{ border-color: var(--accent); }}
    .tile-empty {{ color: var(--muted); grid-column: 1 / -1; }}
    .tile .label {{
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .tile .val {{ font-size: 20px; font-weight: 600; margin-top: 2px; }}
    .trend-down {{ color: #10b981; }}
    .trend-up {{ color: #ef4444; }}
    .chart-wrap {{ margin-bottom: 16px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th {{
      text-align: left;
      padding: 8px 12px;
      border-bottom: 1px solid var(--border);
      color: var(--muted);
      font-weight: 500;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    td {{
      padding: 8px 12px;
      border-bottom: 1px solid var(--border);
    }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover {{ background: #0f172a; }}
    td.num {{ font-variant-numeric: tabular-nums; }}
    td.muted, .muted {{ color: var(--muted); }}
    td a {{ color: var(--text); text-decoration: none; }}
    td a:hover {{ color: var(--accent); }}
    .thumb {{
      width: 72px;
      height: 54px;
      object-fit: cover;
      border-radius: 4px;
      vertical-align: middle;
      margin-right: 8px;
    }}
    .contacted {{
      margin-left: 6px;
      padding: 1px 6px;
      border: 1px solid var(--accent);
      border-radius: 4px;
      font-size: 11px;
      color: var(--accent);
      white-space: nowrap;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      color: #0b1120;
      letter-spacing: 0.5px;
    }}
    @media (max-width: 640px) {{
      header, .nav, main {{ padding: 12px 16px; }}
      .model {{ padding: 16px; }}
      table {{ font-size: 12px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Dirt Bike Tracker</h1>
    <div class="meta">Updated {now}</div>
  </header>
  <nav class="nav">
    {"".join(f'<a href="#{s.slug}">{s.slug.upper()} ({len(s.listings)})</a>' for s in snaps)}
  </nav>
  <main>
    {sections}
  </main>
</body>
</html>
"""


def write_dashboard(snaps: list[MarketSnapshot], out_path: Path) -> Path:
    """Write the HTML dashboard to disk and return the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_dashboard(snaps))
    return out_path
