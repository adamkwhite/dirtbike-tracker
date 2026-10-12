# dirtbike-tracker

Personal dirt bike price tracker. Scrapes Kijiji + AutoTrader, scores vs market median, pushes alerts via ntfy.

## Architecture
Hexagonal: `src/dirtbike_tracker/{application,domain,infrastructure}/`
- `domain/` - pure scoring logic, no I/O. Never import from `infrastructure/`
- `application/ports.py` - ports (#74): `ListingStore` (Protocol), `ListingSource`, `AdDetailFetcher`, `Notifier`, and `App`, the bundle every command gets. Only `cli.py` (the composition root, `build_app()`) imports infrastructure; the rest of `application/` talks to ports
- `application/watch.py` - watch orchestration (scrape → store → score → read full ads → re-score → alert), source health, notified-id state
- `application/market.py` - `snapshot(slug, store, goal)` composes store reads + domain scoring; CLI and renderers consume `MarketSnapshot`
- `application/details.py` / `alerts.py` - full-ad fetch (#71) and DEAL/GOOD message formatting over the ports
- `infrastructure/` - scrapers, DB, notifier, dashboard
- `application/cli.py` - click commands + composition root; commands read the `App` from `ctx.obj`, so tests pass `CliRunner().invoke(cli, args, obj=app)`

## Tests
- `tests/e2e/test_watch_e2e.py` runs `watch` end to end, faking only `requests.get`/`requests.post` (canned pages by URL) and `time.sleep`, against SQLite in `tmp_path`. Extend it when changing the watch flow
- `make drift` re-records real Kijiji/AutoTrader pages into `tests/recorded/` and runs `tests/drift/` against them: a failure means a site changed something a parser reads. The recordings are gitignored and must never be committed (third-party ads with private sellers' details; the repo is public), so CI skips these tests

## Common commands
- `dirtbike watch` - scrape all models, score, notify
- `dirtbike dashboard --open` - regenerate HTML
- `dirtbike stats --model crf150f` - market summary

## Conventions
- Tests must not touch `data/`: it is shared with prod. `tests/conftest.py` fails any test that opens `data/listings.db`, reads included. Never bind `DB_PATH` as a default argument (it's captured at import, so tests can't redirect it): default `db_path` to `None` and let `storage.connect` resolve it. Unit tests use the `app` fixture (`tests/unit/conftest.py`): a tmp SQLite store, no sources or fetchers, a `PushSpy` notifier. Pass fakes in with `dataclasses.replace(app, ...)` rather than patching modules
- Python 3.13, mypy strict, ruff + bandit via pre-commit
- Model slugs listed in `src/dirtbike_tracker/infrastructure/kijiji_scraper.py:KNOWN_MODEL_SLUGS`
- Cron runs every 20 min from the prod clone `~/Code/dirtbike-tracker-prod` (always `main`), installed via its `scripts/install_cron.sh`. Deploy = `git -C ~/Code/dirtbike-tracker-prod pull --ff-only`. `data/` and `dashboard.html` there are symlinks into this repo
- Never put an unescaped `%` in a crontab line (cron reads it as newline)
- Cron env vars go directly before the command: `cd dir && NTFY_TOPIC=x dirtbike watch`. `NTFY_TOPIC=x cd dir && ...` scopes it to `cd` only. `NTFY_TOPIC` / `HEALTHCHECK_URL` live only in the crontab, never the repo
- AutoTrader parses listings from the page's `__NEXT_DATA__` JSON; Kijiji parses HTML cards and reads `__NEXT_DATA__` for per-ad `vehicletype`, `activationDate` and the ~200-char `description` snippet (a snippet not ending in `...` is the whole ad, stored as `full_description` so #71 skips the fetch). Both skip new-vehicle dealer stock (Kijiji `vehicletype=new`, AutoTrader `offerType=N`) so MSRP doesn't set the used-market median
