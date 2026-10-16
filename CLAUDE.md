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
- `dirtbike drift` (daily cron, 05:00; `make drift` locally) re-records real Kijiji/AutoTrader pages into `tests/recorded/` and runs `infrastructure/drift.py`'s checks over them, alerting via ntfy on problems: a failure means a site changed something a parser reads. When adding a parsed field, add its check there. The recordings are gitignored and must never be committed (third-party ads with private sellers' details; the repo is public), so CI skips `tests/drift`. README "Site drift" has the runbook

## Common commands
- `dirtbike watch` - scrape all models, score, notify
- `dirtbike watch --no-notify [--group g]` - seed after adding a model, so its backlog doesn't alert. It prints each alert it skipped (`quiet deals: ...`): pass the real finds on to the user, since a seed marks them alerted
- `dirtbike dashboard --open` - regenerate HTML
- `dirtbike stats --model crf150f` - market summary

## Conventions
- Tests must not touch `data/`. `tests/conftest.py` fails any test that opens `data/listings.db`, reads included. Never bind `DB_PATH` as a default argument (it's captured at import, so tests can't redirect it): default `db_path` to `None` and let `storage.connect` resolve it. Unit tests use the `app` fixture (`tests/unit/conftest.py`): a tmp SQLite store, no sources or fetchers, a `PushSpy` notifier. Pass fakes in with `dataclasses.replace(app, ...)` rather than patching modules
- Python 3.13, mypy strict, ruff + bandit via pre-commit
- Model slugs listed in `src/dirtbike_tracker/infrastructure/kijiji_scraper.py:KNOWN_MODEL_SLUGS`. Every `models.yml` entry sets its production years (`min_year` / `max_year`): `watch` drops listings outside them before storing (`domain.scoring.matches_spec`), which catches vague titles ("2003 Honda crf" filed under CRF250F) that title matching can't
- Cron runs every 20 min from the prod clone `~/Code/dirtbike-tracker-prod` (always `main`), installed via its `scripts/install_cron.sh`. Deploy = `git -C ~/Code/dirtbike-tracker-prod pull --ff-only`. The prod clone owns the live `data/` and `dashboard.html`; this repo's `data/` is a dev copy, so dev commands (and the schema migrations `init_db()` runs) never touch prod. The live DB is `~/Code/dirtbike-tracker-prod/data/listings.db`: query it read-only (`?mode=ro`)
- `--config <file>` (e.g. `config/friend.yml`, a friend's watchlist; gitignored, like every `config/*.yml` but `models.yml`, since it's personal and the repo is public; template `config/friend.example.yml`) runs any command on another watchlist: same DB, its own `state_dir` (`data/state/<stem>/`) and `dashboard-<stem>.html`, alerts to whatever `NTFY_TOPIC` its cron line sets. Its entries can set `query`, `include_new`, `min_year`, `require` (title words; `"L|RL"` takes either) and `alert: all`; `query`/`include_new` reach sources as keyword args only when set (`ListingSource` in ports.py)
- Never put an unescaped `%` in a crontab line (cron reads it as newline)
- Sturgess Cycle (`dealerspike.py`, #99) reads the dealer's whole used inventory from one static file per run. A dealer with none of a model is normal, so `last_fetch_ok` (read by `watch.sources_seen`) marks the source seen when the fetch worked: that keeps `mark_stale` aging out sold units and source health quiet. Any new whole-inventory source should set it too
- Facebook (`fb_scraper.py`, #9) is opt-in per model (`facebook` in models.yml `sources`) and throttled to one search per model per 6h: keep it that way, FB bans scrapers. Playwright is the optional `[fb]` extra, imported lazily, so CI and Docker run without it. The browser profile (`FB_PROFILE_DIR`) can hold a logged-in session: never inside the repo
- Cron env vars go directly before the command: `cd dir && NTFY_TOPIC=x dirtbike watch`. `NTFY_TOPIC=x cd dir && ...` scopes it to `cd` only. `NTFY_TOPIC` / `HEALTHCHECK_URL` live only in the crontab, never the repo
- AutoTrader parses listings from the page's `__NEXT_DATA__` JSON, from the keyword search plus the model's used-only family pages (`FAMILY_PATHS`: sellers file dirt bikes under "Honda CRF" with the variant in free text; an unknown path returns the whole make page, which the `pagePath` guard rejects); Kijiji parses HTML cards and reads `__NEXT_DATA__` for per-ad `vehicletype`, `activationDate` and the ~200-char `description` snippet (a snippet not ending in `...` is the whole ad, stored as `full_description` so #71 skips the fetch). Both skip new-vehicle dealer stock (Kijiji `vehicletype=new`, AutoTrader `offerType=N`, plus a dealer's current-model-year bike, `is_dealer_new`) so MSRP doesn't set the used-market median. Both record `seller_type` (Kijiji `forsaleby` ownr/delr, AutoTrader `seller.type`); the median is private sales only once a model has `PRIVATE_MIN_SAMPLES` (#81)
