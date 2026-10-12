# dirtbike-tracker

Personal dirt bike price tracker. Scrapes Kijiji + AutoTrader, scores vs market median, pushes alerts via ntfy.

## Architecture
Hexagonal: `src/dirtbike_tracker/{application,domain,infrastructure}/`
- `domain/` - pure scoring logic, no I/O. Never import from `infrastructure/`
- `application/market.py` - `snapshot(slug)` composes storage reads + domain scoring; CLI and renderers consume `MarketSnapshot`
- `application/details.py` - `AdDetailFetcher` port (a `Callable`, wired by source in `cli.DETAIL_FETCHERS`). Only port so far; the rest of `application/` imports infrastructure directly (#74)
- `infrastructure/` - scrapers, DB, notifier, dashboard
- `application/cli.py` - orchestration via click

## Known tech debt
- Untested: scraper HTTP/pagination (`scrape_model`), storage writes (`upsert_listings`, `mark_stale`), ntfy message formatting. See issues tagged `area:testing`

## Common commands
- `dirtbike watch` - scrape all models, score, notify
- `dirtbike dashboard --open` - regenerate HTML
- `dirtbike stats --model crf150f` - market summary

## Conventions
- Tests must not touch `data/`: it is shared with prod. `cli()` runs `init_db()`, so `tests/unit/conftest.py` stubs it; stub `DETAIL_FETCHERS` in any test that runs `watch` (else it hits Kijiji)
- Python 3.13, mypy strict, ruff + bandit via pre-commit
- Model slugs listed in `src/dirtbike_tracker/infrastructure/kijiji_scraper.py:KNOWN_MODEL_SLUGS`
- Cron runs every 20 min from the prod clone `~/Code/dirtbike-tracker-prod` (always `main`), installed via its `scripts/install_cron.sh`. Deploy = `git -C ~/Code/dirtbike-tracker-prod pull --ff-only`. `data/` and `dashboard.html` there are symlinks into this repo
- Never put an unescaped `%` in a crontab line (cron reads it as newline)
- Cron env vars go directly before the command: `cd dir && NTFY_TOPIC=x dirtbike watch`. `NTFY_TOPIC=x cd dir && ...` scopes it to `cd` only. `NTFY_TOPIC` / `HEALTHCHECK_URL` live only in the crontab, never the repo
- AutoTrader parses listings from the page's `__NEXT_DATA__` JSON; Kijiji parses HTML cards and reads `__NEXT_DATA__` only for per-ad `vehicletype`. Both skip new-vehicle dealer stock (Kijiji `vehicletype=new`, AutoTrader `offerType=N`) so MSRP doesn't set the used-market median
