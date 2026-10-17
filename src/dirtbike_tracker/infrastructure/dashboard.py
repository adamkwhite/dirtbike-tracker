"""
Static HTML dashboard generator.

Writes a single self-contained HTML file (plus one Chart.js from CDN)
showing all tracked models at a glance: active listings, deal ratings,
market stats, and a price distribution chart per model.

Run via `python src/main.py dashboard` or let cron do it every 20 min.
"""

import html
import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from ..domain.scoring import (
    FALLBACK_NOTE,
    MarketSnapshot,
    median_label,
    price_per_year,
    project_line,
    vs_label,
)
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
          <div class="tile tile-median"><div class="label">{_esc(median_label(stats))}</div><div class="val">${int(stats["median"])}</div></div>
          <div class="tile"><div class="label">High</div><div class="val">${int(stats["high"])}</div></div>
          {project_html}
          {trend_html}
        """


def _sort_key(value) -> str:
    """A cell's data-v: what the sortable tables sort by ('' sorts last)."""
    return "" if value is None else _esc(value)


def _listing_row(listing: dict, models: list[str] | None = None) -> str:
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
    if listing.get("seller_type") == "dealer":
        contacted += ' <span class="tag-dealer">dealer</span>'
    model_cell = (
        f'<td class="muted">{_esc(", ".join(m.upper() for m in models))}</td>' if models else ""
    )
    return f"""
              <tr>
                <td data-v="{list(RATING_HEX).index(rating) if rating in RATING_HEX else 9}"><span class="badge" style="background:{RATING_HEX.get(rating, "#6b7280")}">{_esc(rating)}</span></td>{model_cell}
                <td class="num" data-v="{_sort_key(listing["price"] or None)}">{_esc(price_str)}</td>
                <td class="num muted" data-v="{_sort_key(listing.get("year"))}">{listing.get("year") or "—"}</td>
                <td class="num muted" data-v="{_sort_key(listing.get("km"))}">{f"{listing['km']:,}" if listing.get("km") else "—"}</td>
                <td class="num muted" data-v="{_sort_key(per_year)}">{f"${per_year:,}" if per_year else "—"}</td>
                <td class="num muted" data-v="{_sort_key(delta)}" title="{_esc(vs)}">{_esc(delta_str)}</td>
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


LISTING_HEADERS = (
    '<th>Price</th><th>Year</th><th>km</th><th title="Price per year of age">$/yr</th><th>vs Med</th>'
    '<th title="Project fit 0-10: red/green phrases in the ad">Fit</th><th>Location</th><th>Title</th>'
)


def _all_bikes_section(snaps: list[MarketSnapshot]) -> str:
    """Every active listing of the given models in one table, cheapest first.

    A listing filed under two models ("2003 Honda crf" as CRF150F and CRF230F) is one row
    naming both; its rating is against the first model's median.
    """
    rows: dict[str, tuple[dict, list[str]]] = {}
    for snap in snaps:
        for listing in snap.listings:
            rows.setdefault(listing["url"], (listing, []))[1].append(snap.slug)
    ordered = sorted(rows.values(), key=lambda r: r[0]["price"] or float("inf"))
    body = "".join(_listing_row(listing, models) for listing, models in ordered)
    return f"""
    <section class="model" id="all">
      <h2>ALL DIRT BIKES ({len(ordered)})</h2>
      <p class="muted">Click a column to sort.</p>
      <table class="sortable">
        <thead>
          <tr><th>Rating</th><th>Model</th>{LISTING_HEADERS}</tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


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
      <table class="sortable">
        <thead>
          <tr><th>Rating</th>{LISTING_HEADERS}</tr>
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


def _new_units_section(units: list[dict]) -> str:
    """Dealers' new units of the tracked models (application/markdowns.py), biggest saving first."""

    def saving(u: dict) -> float:
        return (u["was"] - u["price"]) if u.get("was") else 0.0

    rows = "".join(
        f"""
              <tr>
                <td class="muted">{_esc(u["model_slug"].upper())}</td>
                <td class="num" data-v="{u["price"]:.0f}">${u["price"]:,.0f}</td>
                <td class="num muted" data-v="{_sort_key(u.get("was"))}">{f"${u['was']:,.0f}" if u.get("was") else "—"}</td>
                <td class="num" data-v="{saving(u):.0f}">{f"${saving(u):,.0f}" if saving(u) else "—"}</td>
                <td class="muted">{_esc(u["dealer"])}</td>
                <td><a href="{_esc(u["url"])}" target="_blank" rel="noopener">{_esc(u["title"])}</a></td>
              </tr>"""
        for u in sorted(units, key=lambda u: (-saving(u), u["price"]))
    )
    return f"""
    <section class="model" id="new">
      <h2>NEW AT DEALERS ({len(units)})</h2>
      <p class="muted">New units of the tracked models. A price cut, or a unit listed under its
        regular price or MSRP, sends a MARKDOWN alert. Not in the used-market medians.</p>
      <table class="sortable">
        <thead>
          <tr><th>Model</th><th>Price</th><th title="Regular price or MSRP">Was</th><th>Save</th><th>Dealer</th><th>Title</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


def render_dashboard(
    snaps: list[MarketSnapshot], combined: Iterable[str] = (), new_units: Sequence[dict] = ()
) -> str:
    """Build the full HTML page. `combined`: the models also listed together, first;
    `new_units`: dealers' new stock (the markdown watch), after them."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    together = [s for s in snaps if s.slug in set(combined)]
    sections = "\n".join(
        ([_all_bikes_section(together)] if together else [])
        + ([_new_units_section(list(new_units))] if new_units else [])
        + [_model_section(s) for s in snaps]
    )
    all_link = (
        f'<a href="#all">ALL DIRT BIKES ({len({x["url"] for s in together for x in s.listings})})</a>'
        if together
        else ""
    ) + (f'<a href="#new">NEW AT DEALERS ({len(new_units)})</a>' if new_units else "")

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
    .tag-dealer {{
      margin-left: 6px;
      padding: 1px 6px;
      border: 1px solid var(--muted);
      border-radius: 4px;
      font-size: 11px;
      color: var(--muted);
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
    table.sortable th {{ cursor: pointer; user-select: none; }}
    table.sortable th:hover {{ color: var(--accent); }}
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
    {all_link}{"".join(f'<a href="#{s.slug}">{s.slug.upper()} ({len(s.listings)})</a>' for s in snaps)}
  </nav>
  <main>
    {sections}
  </main>
  <script>
    // Click a header to sort its table: by each cell's data-v when set, else its text;
    // empty values sort last.
    document.querySelectorAll("table.sortable th").forEach((th, col) => {{
      th.addEventListener("click", () => {{
        const body = th.closest("table").tBodies[0];
        const asc = th.dataset.dir !== "asc";
        th.dataset.dir = asc ? "asc" : "desc";
        const key = (row) => {{
          const cell = row.cells[col];
          const v = cell.dataset.v ?? cell.textContent.trim().toLowerCase();
          return v === "" || isNaN(v) ? v : Number(v);
        }};
        [...body.rows]
          .sort((a, b) => {{
            const x = key(a), y = key(b);
            if (x === "" || y === "") return (x === "") - (y === "");
            return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
          }})
          .forEach((row) => body.appendChild(row));
      }});
    }});
  </script>
</body>
</html>
"""


def write_dashboard(
    snaps: list[MarketSnapshot],
    out_path: Path,
    combined: Iterable[str] = (),
    new_units: list[dict] | None = None,
) -> Path:
    """Write the HTML dashboard to disk and return the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_dashboard(snaps, combined, new_units or []))
    return out_path
