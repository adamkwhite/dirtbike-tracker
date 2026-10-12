# Dirtbike Tracker Architecture

## System overview

The layer diagram lives in the [README](../README.md#how-it-works) (Mermaid) so there is
one copy to keep current. Dependency rule: `application` → {`domain`, `infrastructure`},
`infrastructure` → `domain`, and `domain` imports nothing from either.

## Modules

| Layer | Module | Responsibility |
|---|---|---|
| application | `cli.py` | click CLI (`watch`, `scrape`, `list`, `stats`, `report`, `dashboard`, `models`, `dismiss`, `undismiss`, `backup`); orchestrates scrape → upsert → score → read full ads → re-score → notify; source-health check, healthcheck ping. Composition root for `DETAIL_FETCHERS` |
| application | `market.py` | `snapshot(slug)`: reads prices + active listings from storage, scores them (full ad text when fetched, else the card snippet), returns a `MarketSnapshot` |
| application | `details.py` | `AdDetailFetcher` port (`url -> description \| None`) and `fetch_details()`: reads the full ad of up to `DETAIL_BUDGET` listings per model that could alert. The first real port, see #74 |
| domain | `scoring.py` | Pure: `market_stats`, `rate_listing`, `score_listings`, `project_fit`, `urgency`, `under_target`, `needs_detail`, `trend`, `weekly_medians`, `MarketSnapshot` |
| infrastructure | `kijiji_scraper.py` | Kijiji search fetch, HTML card parsing, model detection; reads `vehicletype` (skips `new`), `activationDate` and the description snippet from `__NEXT_DATA__` (an uncut snippet is the full ad, #76); `fetch_ad_description` adapter reads the ad page's `__APOLLO_STATE__` |
| infrastructure | `autotrader_scraper.py` | AutoTrader keyword search; parses `__NEXT_DATA__` listings (description included), skips `offerType=N` |
| infrastructure | `storage.py` | SQLite schema + in-place migrations, upsert, fingerprint dedup, multi-model tagging, `market_prices`, `price_drops`, likely-sold, dismiss, `save_full_description` |
| infrastructure | `notifier.py` | ntfy.sh push (DEAL/GOOD/target/price drop, source-health warnings), healthchecks.io ping |
| infrastructure | `dashboard.py`, `reporter.py` | Render a `MarketSnapshot` to HTML / markdown |
| infrastructure | `backup.py` | Nightly/weekly DB + state snapshot to Google Drive via rclone |
| infrastructure | `extraction.py` | Regex + optional LLM fact extraction (year, hours, needs). Not yet wired into the pipeline |

## Data model

```sql
listings:
  id | source | external_id | url | title | price | price_state
     | location | posted_text | description | fingerprint
     | first_seen | last_seen | is_active | is_primary
     | sold_at            -- likely sold: gone from a healthy source for 72h
     | dismissed_at       -- `dirtbike dismiss`; upsert never clears it
     | full_description   -- full ad body (#71), or an uncut search snippet (#76); never overwritten once set
     | detail_fetched_at

listing_models:
  listing_id | model_slug          -- many-to-many; package listings get 2+ rows

price_history:
  id | listing_id | price | observed_at
```

## Dedup strategy

Two separate dedup mechanisms:

1. **Hard dedup via `(source, external_id)`**: same URL re-scraped on multiple days → one row, `last_seen` updated
2. **Fingerprint dedup via `(normalized_title, price_bucket, location)`**: cross-posted same bike under different URLs → first copy marked `is_primary=1`, duplicates marked `is_primary=0` and hidden from `list` and median calculations

Fingerprint is MD5 of a normalized key, truncated to 16 chars. Price is bucketed in $50 increments so slight variations don't break the match.

## Scoring

- Market = one price per **primary** listing, price > $100, first seen in the last 90 days (active or not, so sold listings still count as market data)
- New-vehicle dealer stock never reaches the DB (filtered at scrape time), so the median is a used-market price, not MSRP
- Delta vs median: `(price - median) / median`
- Thresholds: DEAL ≤ -30%, GOOD ≤ -10%, FAIR ≤ +15%, HIGH > +15%
- A listing at or below the model's `target_price` alerts regardless of rating
- Trend: median of listings first seen in the last 30 days vs those first seen 30-90 days ago (split by date)

## Scaling notes

Current design handles ~10K listings per model without issue. If you push past ~100K:

- Add indexes on `(model_slug, is_active, is_primary)` for list queries
- Partition `price_history` by month and vacuum old data
- Consider Postgres if you want to join across many metrics or run analytics queries
- SQLite handles single-writer workloads fine; cron serializes writes naturally

## Why not FB Marketplace?

See `docs/fb_marketplace_notes.md`. Short version: requires authenticated session or Playwright, high maintenance cost, Facebook actively rate-limits scrapers. The ROI vs Kijiji+AutoTrader is low.

## Deployment

Local Ubuntu/WSL under cron, running from a separate prod clone (`~/Code/dirtbike-tracker-prod`, always `main`) so a feature branch in the dev checkout never goes live. Deploy = `git pull --ff-only` in the prod clone. Setup steps: README "Cron".

- `data/` (SQLite DB `listings.db`, `state/notified_ids.txt`, `state/source_health.json`) and `dashboard.html` are symlinks from the prod clone into the dev repo
- Logs: `logs/watch.log` and `logs/report.log` in the prod clone
- `NTFY_TOPIC` and `HEALTHCHECK_URL` are set only on the crontab line, never in the repo

Failure alerting:
- **A scraper silently breaks:** `watch` pushes an ntfy warning when a source returns 0 listings across every model for 24h
- **`watch` stops running:** healthchecks.io alerts when the per-run ping stops (schedule `*/20 6-23 * * *`, America/Toronto)
