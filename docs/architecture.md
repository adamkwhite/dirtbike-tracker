# Dirtbike Tracker Architecture

## System overview

```
                           ┌─────────────┐
                           │  cron       │ every 20 min
                           │  (watch)    │
                           └──────┬──────┘
                                  │
                                  ▼
┌─────────────┐   ┌─────────────────────────────┐   ┌──────────────┐
│ models.yml  │──▶│         main.py             │──▶│  ntfy.sh     │
│ (config)    │   │   watch / scrape / report   │   │  (push)      │
└─────────────┘   └─────────────────────────────┘   └──────┬───────┘
                        │          │                       │
                        ▼          ▼                       ▼
         ┌──────────────────┐  ┌──────────────────┐  ┌────────────┐
         │ scraper.py       │  │ storage.py       │  │ Your phone │
         │ (kijiji)         │  │ (SQLite +        │  └────────────┘
         │                  │  │  dedup + multi-  │
         │ autotrader_      │  │  model tags)     │
         │ scraper.py       │  │                  │
         └──────────────────┘  └────────┬─────────┘
                                        │
                                        ▼
                               ┌──────────────────┐
                               │ scorer.py        │
                               │ (median + rating)│
                               └────────┬─────────┘
                                        │
                                        ▼
                               ┌──────────────────┐
                               │ reporter.py      │
                               │ (markdown out)   │
                               └──────────────────┘

                       ┌──────────────────┐
                       │ extractor.py     │  (optional)
                       │ regex or LLM     │
                       │ year/hours/etc   │
                       └──────────────────┘
```

## Module responsibilities

| Module | Lines | Responsibility |
|---|---|---|
| `scraper.py` | ~250 | Kijiji HTML fetch + parse + model detection |
| `autotrader_scraper.py` | ~130 | AutoTrader keyword search (secondary source) |
| `storage.py` | ~220 | SQLite schema, upsert, dedup via fingerprint, multi-model tagging |
| `scorer.py` | ~90 | Market median + deal rating (DEAL/GOOD/FAIR/HIGH) |
| `reporter.py` | ~70 | Markdown snapshot per model |
| `notifier.py` | ~90 | ntfy.sh HTTP push |
| `extractor.py` | ~130 | Regex + optional LLM extraction of structured facts |
| `main.py` | ~240 | CLI (click) with scrape/list/stats/report/watch commands |

## Data model

```sql
listings:
  id | source | external_id | url | title | price | price_state
     | location | posted_text | description | fingerprint
     | first_seen | last_seen | is_active | is_primary

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

- Market median computed from distinct prices in last 90 days, primary listings only
- Delta vs median: `(price - median) / median`
- Thresholds: DEAL ≤ -30%, GOOD ≤ -10%, FAIR ≤ +15%, HIGH > +15%
- Trend: median of last 30d vs median of 30-90d ago

## Scaling notes

Current design handles ~10K listings per model without issue. If you push past ~100K:

- Add indexes on `(model_slug, is_active, is_primary)` for list queries
- Partition `price_history` by month and vacuum old data
- Consider Postgres if you want to join across many metrics or run analytics queries
- SQLite handles single-writer workloads fine; cron serializes writes naturally

## Why not FB Marketplace?

See `docs/fb_marketplace_notes.md`. Short version: requires authenticated session or Playwright, high maintenance cost, Facebook actively rate-limits scrapers. The ROI vs Kijiji+AutoTrader is low.

## Deployment

Local Ubuntu/WSL under cron. Logs go to `logs/`. State (notified IDs) lives in `data/state/`. DB in `data/listings.db`. All paths relative to project root for portability.

To migrate to another machine: copy the whole directory, re-run `install_cron.sh`.
