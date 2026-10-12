# dirtbike-tracker

Personal dirt bike price tracker. Scrapes Kijiji + AutoTrader, scores vs market median, pushes alerts via ntfy.

## Architecture
Hexagonal: `src/dirtbike_tracker/{application,domain,infrastructure}/`
- `domain/` - pure scoring logic
- `infrastructure/` - scrapers, DB, notifier, dashboard
- `application/cli.py` - orchestration via click

## Known tech debt
- `domain/scoring.py` imports from `infrastructure/storage.py` (hexagonal violation). See issue #34
- Near-zero test coverage (one CLI smoke test). See issues tagged `area:testing`
- `docs/architecture.md` module table predates the hexagonal port (old flat filenames)

## Common commands
- `dirtbike watch` - scrape all models, score, notify
- `dirtbike dashboard --open` - regenerate HTML
- `dirtbike stats --model crf150f` - market summary

## Conventions
- Python 3.13, mypy strict, ruff + bandit via pre-commit
- Model slugs listed in `src/dirtbike_tracker/infrastructure/kijiji_scraper.py:KNOWN_MODEL_SLUGS`
- Cron runs every 20 min from `scripts/install_cron.sh`
